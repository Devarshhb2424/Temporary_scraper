"""
Recreation.gov Campground & Campsite Availability Web Application
================================================================
FastAPI backend powering search, live availability, persistent session login,
and automated 15-minute cart holding - all unified in a single file.
"""

import argparse
import os
import sys
import threading
import time
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

from recreation_gov_scraper import RecreationGovScraper
from campsite_availability_scraper import CampsiteAvailabilityScraper

# Base directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
SESSION_DIR = os.path.join(BASE_DIR, "playwright_session")

# FastAPI App initialization
app = FastAPI(title="Recreation.gov Campsite Availability Finder & Auto-Hold")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Scraper instances
search_scraper = RecreationGovScraper()
availability_scraper = CampsiteAvailabilityScraper()

# Standard US States
US_STATES = [
    {"name": "Alabama", "abbreviation": "AL"},
    {"name": "Alaska", "abbreviation": "AK"},
    {"name": "Arizona", "abbreviation": "AZ"},
    {"name": "Arkansas", "abbreviation": "AR"},
    {"name": "California", "abbreviation": "CA"},
    {"name": "Colorado", "abbreviation": "CO"},
    {"name": "Connecticut", "abbreviation": "CT"},
    {"name": "Delaware", "abbreviation": "DE"},
    {"name": "Florida", "abbreviation": "FL"},
    {"name": "Georgia", "abbreviation": "GA"},
    {"name": "Hawaii", "abbreviation": "HI"},
    {"name": "Idaho", "abbreviation": "ID"},
    {"name": "Illinois", "abbreviation": "IL"},
    {"name": "Indiana", "abbreviation": "IN"},
    {"name": "Iowa", "abbreviation": "IA"},
    {"name": "Kansas", "abbreviation": "KS"},
    {"name": "Kentucky", "abbreviation": "KY"},
    {"name": "Louisiana", "abbreviation": "LA"},
    {"name": "Maine", "abbreviation": "ME"},
    {"name": "Maryland", "abbreviation": "MD"},
    {"name": "Massachusetts", "abbreviation": "MA"},
    {"name": "Michigan", "abbreviation": "MI"},
    {"name": "Minnesota", "abbreviation": "MN"},
    {"name": "Mississippi", "abbreviation": "MS"},
    {"name": "Missouri", "abbreviation": "MO"},
    {"name": "Montana", "abbreviation": "MT"},
    {"name": "Nebraska", "abbreviation": "NE"},
    {"name": "Nevada", "abbreviation": "NV"},
    {"name": "New Hampshire", "abbreviation": "NH"},
    {"name": "New Jersey", "abbreviation": "NJ"},
    {"name": "New Mexico", "abbreviation": "NM"},
    {"name": "New York", "abbreviation": "NY"},
    {"name": "North Carolina", "abbreviation": "NC"},
    {"name": "North Dakota", "abbreviation": "ND"},
    {"name": "Ohio", "abbreviation": "OH"},
    {"name": "Oklahoma", "abbreviation": "OK"},
    {"name": "Oregon", "abbreviation": "OR"},
    {"name": "Pennsylvania", "abbreviation": "PA"},
    {"name": "Rhode Island", "abbreviation": "RI"},
    {"name": "South Carolina", "abbreviation": "SC"},
    {"name": "South Dakota", "abbreviation": "SD"},
    {"name": "Tennessee", "abbreviation": "TN"},
    {"name": "Texas", "abbreviation": "TX"},
    {"name": "Utah", "abbreviation": "UT"},
    {"name": "Vermont", "abbreviation": "VT"},
    {"name": "Virginia", "abbreviation": "VA"},
    {"name": "Washington", "abbreviation": "WA"},
    {"name": "West Virginia", "abbreviation": "WV"},
    {"name": "Wisconsin", "abbreviation": "WI"},
    {"name": "Wyoming", "abbreviation": "WY"},
]

# Map abbreviation to full state name
STATE_MAP = {s["abbreviation"]: s["name"] for s in US_STATES}


class HoldRequest(BaseModel):
    facility_id: str
    campsite_id: str
    start_date: str
    end_date: str


# =============================================================================
# PERSISTENT SESSION & AUTO-HOLD ENGINE (IN-FILE IMPLEMENTATION)
# =============================================================================

SESSION_FLAG_FILE = os.path.join(SESSION_DIR, "session_active.json")


def check_session_logged_in(session_dir: str = SESSION_DIR) -> bool:
    """
    Checks if the persistent session exists and is active without spawning a heavy browser process.
    """
    if not os.path.exists(session_dir):
        return False

    flag_path = os.path.join(session_dir, "session_active.json")
    if os.path.exists(flag_path):
        try:
            import json
            with open(flag_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("logged_in"):
                    return True
        except Exception:
            pass

    # Fallback: check if Chromium Cookies SQLite database exists with data
    cookie_file = os.path.join(session_dir, "Default", "Network", "Cookies")
    if not os.path.exists(cookie_file):
        cookie_file = os.path.join(session_dir, "Default", "Cookies")

    if os.path.exists(cookie_file) and os.path.getsize(cookie_file) > 1024:
        return True

    return False


def setup_login_session(session_dir: str = SESSION_DIR, timeout_seconds: int = 300) -> bool:
    """
    Launches a visible Chromium window with persistent storage.
    The user can log into Recreation.gov and complete any 2FA/CAPTCHA.
    Cookies are saved permanently to `./playwright_session`.
    """
    import json
    os.makedirs(session_dir, exist_ok=True)
    print("\n" + "=" * 58)
    print("  RECREATION.GOV PERSISTENT SESSION SETUP")
    print("=" * 58)
    print("[*] Launching browser window...")
    print("[*] Please log in to your Recreation.gov account.")
    print("[*] Once logged in, your session cookies will save automatically.\n")

    logged_in = False
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=session_dir,
                headless=False,
                args=["--start-maximized"],
                no_viewport=True
            )
            page = context.new_page()
            page.goto("https://www.recreation.gov/", timeout=60000)

            print("[+] Browser opened at Recreation.gov.")
            print("[+] Waiting for login... (polling every 3s)")

            start_time = time.time()
            while time.time() - start_time < timeout_seconds:
                try:
                    if page.is_closed():
                        cookies = context.cookies()
                        if any("auth" in c["name"].lower() or "session" in c["name"].lower() or "token" in c["name"].lower() for c in cookies):
                            logged_in = True
                        break

                    # Target specific navigation header container (prevents strict mode violation)
                    nav_header = page.locator("#nav-header-container")
                    header_text = nav_header.first.inner_text() if nav_header.count() > 0 else (
                        page.locator("header").first.inner_text() if page.locator("header").count() > 0 else ""
                    )

                    if any(term in header_text for term in ["My Account", "Log Out", "Sign Out"]):
                        logged_in = True
                        break

                    cookies = context.cookies()
                    has_auth = any("auth" in c["name"].lower() or "session" in c["name"].lower() or "token" in c["name"].lower() for c in cookies)
                    if has_auth and "Sign Up / Log In" not in header_text:
                        logged_in = True
                        break
                except Exception:
                    pass

                time.sleep(3)

            if logged_in:
                print("\n[OK] SUCCESS: Login detected! Session saved to:", session_dir)
                try:
                    flag_path = os.path.join(session_dir, "session_active.json")
                    with open(flag_path, "w", encoding="utf-8") as f:
                        json.dump({"logged_in": True, "updated_at": time.time()}, f)
                except Exception:
                    pass
            else:
                print("\n[*] Browser closed or timeout reached.")

            try:
                context.close()
            except Exception:
                pass
    except Exception as e:
        print(f"[-] Session setup error: {e}")

    return logged_in


def add_to_cart_and_hold(
    campsite_id: str,
    facility_id: str,
    start_date: str,
    end_date: str,
    session_dir: str = SESSION_DIR,
    headless: bool = False
) -> bool:
    """
    Uses the persistent browser profile to select the requested dates on the
    interactive calendar and click 'Add to Cart', locking Recreation.gov's
    15-minute reservation timer.
    """
    from datetime import datetime
    os.makedirs(session_dir, exist_ok=True)
    print(f"\n[+] Auto-Hold: Launching browser for Campsite #{campsite_id} (Facility #{facility_id})...")
    print(f"[+] Date Target: {start_date} to {end_date}")

    success = False
    try:
        s_dt = datetime.strptime(start_date, "%Y-%m-%d")
        e_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Format matches Recreation.gov aria-labels across US and International/Indian locales:
        # e.g. "September 21, 2026" or "21 September 2026"
        start_month_year = s_dt.strftime("%B %Y")
        start_loc_str = (
            f"div.calendar-cell[aria-label*='{s_dt.strftime('%B')} {s_dt.day}, {s_dt.year}'], "
            f"div.calendar-cell[aria-label*='{s_dt.day} {s_dt.strftime('%B')} {s_dt.year}'], "
            f"div.calendar-cell[aria-label*='{s_dt.strftime('%b')} {s_dt.day}, {s_dt.year}'], "
            f"div.calendar-cell[aria-label*='{s_dt.day} {s_dt.strftime('%b')} {s_dt.year}']"
        )
        end_loc_str = (
            f"div.calendar-cell[aria-label*='{e_dt.strftime('%B')} {e_dt.day}, {e_dt.year}'], "
            f"div.calendar-cell[aria-label*='{e_dt.day} {e_dt.strftime('%B')} {e_dt.year}'], "
            f"div.calendar-cell[aria-label*='{e_dt.strftime('%b')} {e_dt.day}, {e_dt.year}'], "
            f"div.calendar-cell[aria-label*='{e_dt.day} {e_dt.strftime('%b')} {e_dt.year}']"
        )

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=session_dir,
                headless=headless,
                args=["--start-maximized"] if not headless else [],
                no_viewport=True if not headless else False,
                viewport={"width": 1366, "height": 768} if headless else None
            )

            try:
                page = context.new_page()
                campsite_url = f"https://www.recreation.gov/camping/campsites/{campsite_id}"

                print(f"[*] Navigating to campsite page: {campsite_url}")
                page.goto(campsite_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)

                # Check if session is signed in
                nav_header = page.locator("#nav-header-container")
                header_text = nav_header.first.inner_text() if nav_header.count() > 0 else ""
                if "Sign Up / Log In" in header_text or page.locator("button:has-text('Sign Up / Log In')").count() > 0:
                    print("[!] NOTICE: User session appears logged out. Proceeding with best effort...")
                else:
                    print("[OK] User session is logged in.")

                # Scroll to #site-availability or click 'Enter Dates' to reveal calendar
                enter_dates_btn = page.locator("#add-cart-campsite, button:has-text('Enter Dates')")
                if enter_dates_btn.count() > 0 and enter_dates_btn.first.is_visible():
                    print("[*] Clicking 'Enter Dates' to reveal availability calendar...")
                    enter_dates_btn.first.click()
                    page.wait_for_timeout(1500)
                else:
                    page.evaluate("() => { const el = document.getElementById('site-availability'); if (el) el.scrollIntoView(); }")
                    page.wait_for_timeout(1000)

                # Wait for availability calendar
                try:
                    page.wait_for_selector("#site-availability", timeout=8000)
                except Exception:
                    pass

                # Ensure requested month is visible in the calendar
                print(f"[*] Navigating calendar to month: {start_month_year}...")
                for _ in range(12):
                    cal_loc = page.locator("section:has(#site-availability), body").first
                    cal_text = cal_loc.inner_text() if cal_loc.count() > 0 else ""
                    if start_month_year in cal_text:
                        break
                    next_btn = page.locator("button.next-prev-button[aria-label*='Next' i], button[aria-label='Next'], button[aria-label*='next' i]")
                    if next_btn.count() > 0:
                        next_btn.first.click()
                        page.wait_for_timeout(700)
                    else:
                        break

                # 1. Click Start Date cell
                print(f"[*] Selecting start date cell: '{start_date}'...")
                start_cell = page.locator(start_loc_str).first
                if start_cell.count() > 0:
                    # Check if cell is disabled or unavailable
                    classes = start_cell.get_attribute("class") or ""
                    aria_disabled = start_cell.get_attribute("aria-disabled") == "true"
                    if aria_disabled or "is-unavailable" in classes or "firstComeFirstServed" in classes:
                        print(f"[-] Check-in date '{start_date}' is unavailable or First-Come First-Served for this campsite.")
                        return False, f"Check-in date ({start_date}) is not reservable for this site (it is First-Come/Unavailable). Please choose an available date."

                    start_cell.scroll_into_view_if_needed()
                    start_cell.click(timeout=6000)
                    page.wait_for_timeout(1000)
                    print("[OK] Start date clicked.")
                else:
                    print(f"[!] Start date cell '{start_date}' not found in calendar.")
                    return False, f"Start date cell '{start_date}' not found in calendar."

                # 2. Click End Date cell
                print(f"[*] Selecting end date cell: '{end_date}'...")
                end_cell = page.locator(end_loc_str).first
                if end_cell.count() > 0:
                    classes = end_cell.get_attribute("class") or ""
                    aria_disabled = end_cell.get_attribute("aria-disabled") == "true"
                    if aria_disabled or "is-unavailable" in classes:
                        print(f"[-] Check-out date '{end_match_str}' is unavailable.")
                        return False, f"Check-out date ({end_date}) is unavailable for this site."

                    end_cell.scroll_into_view_if_needed()
                    end_cell.click(timeout=6000)
                    page.wait_for_timeout(1500)
                    print("[OK] End date clicked.")
                else:
                    print(f"[!] End date cell '{end_match_str}' not found in calendar.")
                    return False, f"End date cell '{end_match_str}' not found in calendar."

                # Wait for bottom action button to change text to 'Add to Cart'
                page.wait_for_timeout(1500)
                add_to_cart = page.locator("button#add-cart-campsite, button:has-text('Add to Cart'), button.campsite-page-book-now-button-tracker")

                # If button is ready
                if add_to_cart.count() > 0 and ("add to cart" in add_to_cart.first.inner_text().lower() or add_to_cart.first.is_enabled()):
                    btn_label = add_to_cart.first.inner_text().strip()
                    print(f"[+] Found reservation button: '{btn_label}'. Clicking to initiate 15-minute hold...")
                    add_to_cart.first.click()
                    page.wait_for_timeout(4000)

                    screenshot_path = os.path.join(BASE_DIR, "cart_reservation_held.png")
                    page.screenshot(path=screenshot_path)
                    print(f"[OK] Screenshot captured: {screenshot_path}")

                    # Check for Recreation.gov Error Modal (e.g. minimum stay rule)
                    error_dialog = page.locator("div[role='dialog']:has-text('error'), div[role='dialog']:has-text('not allowed'), div:has-text('Please fix the error below')")
                    if error_dialog.count() > 0 and error_dialog.first.is_visible():
                        dialog_text = error_dialog.first.inner_text().replace("\n", " ").strip()
                        print(f"[-] Recreation.gov Rule Block: {dialog_text}")
                        # Extract friendly message
                        msg = "Recreation.gov Rule: " + dialog_text
                        if "weekend minimum stay rule" in dialog_text.lower():
                            msg = "Weekend Rule: Friday and Saturday nights must be booked together (e.g. check out Sunday or later)."
                        return False, msg

                    # If a success modal with "Proceed to Cart" button appears, click it
                    proceed_btn = page.locator("button:has-text('Proceed to Cart'), a:has-text('Proceed to Cart')")
                    if proceed_btn.count() > 0 and proceed_btn.first.is_enabled():
                        print("[*] Clicking 'Proceed to Cart'...")
                        proceed_btn.first.click()
                        page.wait_for_timeout(3000)

                    current_url = page.url
                    # Verify cart page or hold modal
                    if "cart" in current_url.lower() and page.locator("text='Your cart is empty'").count() == 0:
                        print("\n" + "=" * 58)
                        print("  SUCCESS: CAMPSITE HELD IN YOUR CART!")
                        print("  Recreation.gov 15-Minute Reservation Timer is ACTIVE.")
                        print("  Checkout URL: https://www.recreation.gov/cart")
                        print("=" * 58 + "\n")
                        return True, "Campsite added to cart! 15-minute reservation timer is active."
                    else:
                        # Check cart badge or header
                        header_nav = page.locator("#nav-header-container, header")
                        if header_nav.count() > 0 and page.locator("a[href*='/cart']").count() > 0:
                            # Re-check URL after a short wait
                            page.wait_for_timeout(2000)
                            if "cart" in page.url.lower():
                                return True, "Campsite added to cart! 15-minute reservation timer is active."

                        return False, "Campsite could not be held in cart. Please check minimum stay rules or book directly."
                else:
                    print("[-] 'Add to Cart' button did not activate after selecting calendar dates.")
                    screenshot_path = os.path.join(BASE_DIR, "cart_hold_attempt.png")
                    page.screenshot(path=screenshot_path)
                    print(f"[*] Debug screenshot saved: {screenshot_path}")
                    return False, "Add to Cart button did not activate. The dates might be unavailable or restricted."

            finally:
                if headless:
                    try:
                        context.close()
                    except Exception:
                        pass
                else:
                    # Visible window: keep open so user can inspect and complete checkout!
                    pass
    except Exception as e:
        print(f"[-] Auto-hold error: {e}")
        return False, f"Auto-hold error: {str(e)}"

    return False, "Unable to complete reservation."


# =============================================================================
# FASTAPI HTTP ROUTES
# =============================================================================

@app.get("/", response_class=HTMLResponse)
def serve_home(request: Request):
    """Render the main index.html user interface."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"states": US_STATES}
    )


# Cloud deployment detector
IS_CLOUD_DEPLOYMENT = bool(
    os.environ.get("RENDER") or
    os.environ.get("RENDER_SERVICE_ID") or
    os.environ.get("CLOUD_DEPLOYMENT")
)


@app.get("/api/session/status")
def get_session_status():
    """Check session status, adapting to local vs cloud (Render) environment."""
    if IS_CLOUD_DEPLOYMENT:
        return {
            "is_cloud": True,
            "logged_in": True,
            "mode": "client_direct",
            "message": "Cloud Deployment: Direct Browser Booking Active (uses your browser's Recreation.gov login)"
        }

    is_logged_in = check_session_logged_in(SESSION_DIR)
    return {
        "is_cloud": False,
        "logged_in": is_logged_in,
        "mode": "local_playwright",
        "session_dir": SESSION_DIR
    }


@app.post("/api/session/login")
def launch_login_browser():
    """Launch login browser locally, or provide direct link if on Render cloud."""
    if IS_CLOUD_DEPLOYMENT:
        return {
            "status": "cloud_mode",
            "is_cloud": True,
            "message": "On Render cloud, please log into Recreation.gov directly in your browser.",
            "account_url": "https://www.recreation.gov/"
        }

    thread = threading.Thread(target=setup_login_session, daemon=True)
    thread.start()
    return {
        "status": "launched",
        "is_cloud": False,
        "message": "Login browser window opened. Please log in with your Recreation.gov account."
    }


@app.get("/location/facilities/")
def get_facilities(
    state: str = Query(..., description="State code (e.g. MD) or full name"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100)
):
    """
    Fetch campgrounds for a given state using Recreation.gov's internal Search API.
    """
    state_query = STATE_MAP.get(state.upper(), state)

    try:
        df = search_scraper.scrape_campgrounds(
            query=state_query,
            radius=165,
            page_size=limit
        )

        facilities = []
        for _, row in df.iterrows():
            def clean_val(v, default=""):
                import pandas as pd
                return default if (v is None or pd.isna(v)) else v

            facilities.append({
                "FacilityID": str(clean_val(row.get("facility_id"))),
                "FacilityName": str(clean_val(row.get("name"), "Unnamed Facility")),
                "City": str(clean_val(row.get("city"), "")),
                "State": str(clean_val(row.get("state"), state_query)),
                "Price": str(clean_val(row.get("price"), "N/A")),
                "Rating": float(clean_val(row.get("average_rating"), 0.0)),
                "FacilityImage": str(clean_val(row.get("preview_image_url"), "")),
                "TotalCampsites": int(clean_val(row.get("total_campsites"), 0))
            })

        return {
            "status": 200,
            "state": state_query,
            "total_count": len(facilities),
            "facilities": facilities
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/location/facilities/{facility_id}/campsites/")
def get_campsite_availability(
    facility_id: str,
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    campsite_type: Optional[str] = Query(None, description="Optional type filter (e.g. RV, ELECTRIC)"),
    only_continuous: bool = Query(False, description="Only show continuous available stays")
):
    """
    Check Date Range Availability for all campsites in a specific campground.
    """
    try:
        results = availability_scraper.check_availability(
            facility_id=facility_id,
            start_date_str=start_date,
            end_date_str=end_date,
            site_type_filter=campsite_type,
            only_continuous=only_continuous
        )

        df_summary = results["summary"]
        campsites = []

        for _, row in df_summary.iterrows():
            campsites.append({
                "CampsiteID": str(row["site_id"]),
                "CampsiteName": f"Site {row['site_number']}",
                "site_number": row["site_number"],
                "Loop": row["loop"],
                "CampsiteType": row["campsite_type"],
                "available_days_count": int(row["available_days_count"]),
                "total_requested_days": int(row["total_requested_days"]),
                "is_continuous_stay": bool(row["is_continuous_stay"]),
                "available_dates": row["available_dates"],
                "image_url": row.get("image_url") or "",
                "booking_url": row["booking_url"]
            })

        return {
            "status": 200,
            "facility_id": facility_id,
            "campground_name": df_summary["campground_name"].iloc[0] if not df_summary.empty else f"Campground #{facility_id}",
            "start_date": start_date,
            "end_date": end_date,
            "total_available_campsites": len(campsites),
            "campsites": campsites
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/campsite/hold")
def trigger_auto_hold(req: HoldRequest):
    """
    Trigger Auto Add-to-Cart bot to hold a campsite in the user's cart for 15 minutes.
    On Render cloud deployment, returns direct 1-click booking link to user's browser.
    """
    direct_url = f"https://www.recreation.gov/camping/campsites/{req.campsite_id}?date={req.start_date}#autocart={req.start_date},{req.end_date}"

    if IS_CLOUD_DEPLOYMENT:
        return {
            "success": True,
            "is_cloud_direct": True,
            "message": "Cloud Mode: Opening campsite booking page. Click the Auto-Cart Bookmarklet to hold!",
            "booking_url": direct_url,
            "cart_url": "https://www.recreation.gov/cart",
            "timer_minutes": 15
        }

    try:
        success, msg = add_to_cart_and_hold(
            campsite_id=req.campsite_id,
            facility_id=req.facility_id,
            start_date=req.start_date,
            end_date=req.end_date,
            session_dir=SESSION_DIR,
            headless=False
        )

        if success:
            return {
                "success": True,
                "message": msg,
                "cart_url": "https://www.recreation.gov/cart",
                "timer_minutes": 15
            }
        else:
            return {
                "success": False,
                "message": msg,
                "booking_url": direct_url
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# CLI & SERVER RUNNER
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recreation.gov Campsite Availability & Auto-Hold Server")
    parser.add_argument("--login", action="store_true", help="Launch interactive login browser to save session")
    parser.add_argument("--hold", action="store_true", help="Directly trigger auto-hold for a campsite")
    parser.add_argument("--facility-id", type=str, default="", help="Campground Facility ID")
    parser.add_argument("--campsite-id", type=str, default="", help="Campsite ID to hold")
    parser.add_argument("--start", type=str, default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="", help="End date YYYY-MM-DD")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind server")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind server")

    args = parser.parse_args()

    if args.login:
        setup_login_session()
    elif args.hold:
        if not args.campsite_id:
            print("[!] Please provide --campsite-id to hold.")
            sys.exit(1)
        add_to_cart_and_hold(
            campsite_id=args.campsite_id,
            facility_id=args.facility_id,
            start_date=args.start,
            end_date=args.end
        )
    else:
        import uvicorn
        print(f"[*] Starting FastAPI Server on http://{args.host}:{args.port}...")
        uvicorn.run("main:app", host=args.host, port=args.port, reload=True)