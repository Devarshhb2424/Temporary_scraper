"""
Campsite Date Range Availability Scraper
========================================
Scrapes date-by-date campsite availability for a specific campground on Recreation.gov
over any given date range (including multi-month spans).

Outputs clean, actionable availability data to Pandas DataFrame, CSV, and JSON.
"""

import argparse
from datetime import datetime, timedelta
import time
from typing import Any, Dict, List, Optional
import httpx
import pandas as pd


class CampsiteAvailabilityScraper:
    BASE_AVAILABILITY_URL = "https://www.recreation.gov/api/camps/availability/campground/{facility_id}/month"
    CAMPGROUND_INFO_URL = "https://www.recreation.gov/api/camps/campgrounds/{facility_id}"

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.recreation.gov/",
    }

    MEDIA_CAMPSITE_URL = "https://www.recreation.gov/api/media/public/campsite/{campsite_id}"
    MEDIA_ASSET_URL = "https://www.recreation.gov/api/media/public/asset/{facility_id}"

    def __init__(self, timeout: float = 15.0):
        self.client = httpx.Client(
            headers=self.DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True
        )
        self._image_cache: Dict[str, Optional[str]] = {}
        self._facility_campsites_cache: Dict[str, Dict[str, Any]] = {}
        self._campground_details_cache: Dict[str, Dict[str, Any]] = {}

    def get_campground_image(self, facility_id: str) -> Optional[str]:
        """Fetch campground hero image."""
        if facility_id in self._image_cache:
            return self._image_cache[facility_id]
        try:
            resp = self.client.get(self.MEDIA_ASSET_URL.format(facility_id=facility_id), timeout=5.0)
            if resp.status_code == 200:
                res = resp.json().get("result", [])
                if res:
                    img_url = res[0].get("url") or res[0].get("url_x2")
                    self._image_cache[facility_id] = img_url
                    return img_url
        except Exception:
            pass
        self._image_cache[facility_id] = None
        return None

    def get_campsite_images(self, campsite_ids: List[str]) -> Dict[str, Optional[str]]:
        """Fetch real photos for a list of campsite IDs in parallel."""
        from concurrent.futures import ThreadPoolExecutor

        results: Dict[str, Optional[str]] = {}
        missing_ids = [sid for sid in campsite_ids if sid not in self._image_cache]

        def _fetch_single(campsite_id: str):
            try:
                resp = self.client.get(
                    self.MEDIA_CAMPSITE_URL.format(campsite_id=campsite_id),
                    timeout=5.0
                )
                if resp.status_code == 200:
                    res = resp.json().get("result", [])
                    if res:
                        return campsite_id, res[0].get("url") or res[0].get("url_x2")
            except Exception:
                pass
            return campsite_id, None

        if missing_ids:
            with ThreadPoolExecutor(max_workers=10) as executor:
                for sid, img_url in executor.map(_fetch_single, missing_ids):
                    self._image_cache[sid] = img_url

        for sid in campsite_ids:
            results[sid] = self._image_cache.get(sid)

        return results

    def get_campground_name(self, facility_id: str) -> str:
        """Fetch campground facility name."""
        try:
            resp = self.client.get(self.CAMPGROUND_INFO_URL.format(facility_id=facility_id))
            if resp.status_code == 200:
                data = resp.json()
                facility = data.get("campground", {})
                return facility.get("facility_name") or f"Campground #{facility_id}"
        except Exception:
            pass
        return f"Campground #{facility_id}"

    def get_facility_campsites_metadata(self, facility_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Fetch all campsite equipment, dimensions, and vehicle rules for a facility.
        Returns a dict mapping campsite_id to its parsed metadata.
        """
        if facility_id in self._facility_campsites_cache:
            return self._facility_campsites_cache[facility_id]

        meta_by_id: Dict[str, Dict[str, Any]] = {}
        try:
            url = f"https://www.recreation.gov/api/camps/campgrounds/{facility_id}/campsites"
            resp = self.client.get(url, params={"start": 0, "size": 1000}, timeout=10.0)
            if resp.status_code == 200:
                raw_campsites = resp.json().get("campsites", [])
                rv_types = {"rv", "trailer", "fifth wheel", "popup", "pop up", "motorhome", "truck camper", "caravan"}
                
                for c in raw_campsites:
                    sid = str(c.get("campsite_id"))
                    permitted = c.get("permitted_equipment", []) or []
                    
                    rv_lengths = []
                    for eq in permitted:
                        eq_name = (eq.get("equipment_name") or "").strip()
                        if any(t in eq_name.lower() for t in rv_types) and not eq.get("is_deactivated", False):
                            ml = eq.get("max_length")
                            if ml is not None:
                                rv_lengths.append(int(ml))

                    max_rv = max(rv_lengths, default=0)
                    
                    # Vehicle length attribute / map
                    mvl_val = None
                    eq_map = c.get("equipment_details_map") or {}
                    if "max_vehicle_length" in eq_map:
                        mvl_val = eq_map["max_vehicle_length"].get("attribute_value")
                    if not mvl_val:
                        for attr in c.get("attributes", []):
                            if attr.get("attribute_code") == "max_vehicle_length":
                                mvl_val = attr.get("attribute_value")
                                break
                    try:
                        max_veh = int(mvl_val) if mvl_val else 0
                    except (ValueError, TypeError):
                        max_veh = 0

                    formatted_eq = []
                    for eq in permitted:
                        if not eq.get("is_deactivated", False) and eq.get("equipment_name"):
                            name = eq.get("equipment_name")
                            length = eq.get("max_length")
                            if length:
                                formatted_eq.append(f"{name} ({length}ft)")
                            else:
                                formatted_eq.append(name)

                    meta_by_id[sid] = {
                        "max_rv_length": max_rv,
                        "max_vehicle_length": max_veh,
                        "permitted_equipment": formatted_eq,
                        "has_rv_equipment": len(rv_lengths) > 0,
                    }
        except Exception as e:
            print(f"[-] Failed to fetch campsite metadata for facility {facility_id}: {e}")

        self._facility_campsites_cache[facility_id] = meta_by_id
        return meta_by_id

    def get_campground_full_info(self, facility_id: str) -> Dict[str, Any]:
        """
        Fetch campground-level alerts, notices, rules, and booking releases with caching.
        """
        if facility_id in self._campground_details_cache:
            return self._campground_details_cache[facility_id]

        data = {
            "facility_id": facility_id,
            "facility_name": f"Campground #{facility_id}",
            "alerts": [],
            "notices": [],
            "booking_window": "",
            "description": "",
        }

        # 1. External alerts (e.g. construction warnings, urgent notices)
        try:
            resp_alert = self.client.get(
                f"https://www.recreation.gov/api/communication/external/alert?location_id={facility_id}&location_type=Campground",
                timeout=6.0
            )
            if resp_alert.status_code == 200:
                for a in resp_alert.json().get("alerts", []):
                    body = a.get("body") or ""
                    if body:
                        data["alerts"].append({
                            "level": a.get("alert_level", "WARNING"),
                            "title": a.get("title") or "Campground Alert / Notice",
                            "body": body
                        })
        except Exception:
            pass

        # 2. Campground general info & notices
        try:
            resp_cg = self.client.get(
                self.CAMPGROUND_INFO_URL.format(facility_id=facility_id),
                timeout=6.0
            )
            if resp_cg.status_code == 200:
                cg = resp_cg.json().get("campground", {})
                data["facility_name"] = cg.get("facility_name") or data["facility_name"]
                data["description"] = cg.get("facility_description") or ""
                for n in cg.get("notices", []):
                    if n.get("notice_text") and not n.get("hide_on_permit", False):
                        data["notices"].append({
                            "type": n.get("notice_type", "info"),
                            "text": n.get("notice_text", "")
                        })
        except Exception:
            pass

        # 3. Booking releases (booking windows)
        try:
            resp_rel = self.client.get(
                f"https://www.recreation.gov/api/camps/campgrounds/{facility_id}/releases",
                timeout=5.0
            )
            if resp_rel.status_code == 200:
                cur = resp_rel.json().get("current_release", {})
                end_str = cur.get("end")
                if end_str:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(end_str.split("T")[0], "%Y-%m-%d")
                        data["booking_window"] = f"Reservable through {dt.strftime('%B %d, %Y')}"
                    except Exception:
                        data["booking_window"] = f"Reservable through {end_str}"
        except Exception:
            pass

        self._campground_details_cache[facility_id] = data
        return data

    def get_campsite_full_details(self, facility_id: str, campsite_id: str) -> Dict[str, Any]:
        """
        Fetch full details, warnings, permitted equipment, driveway specs, and notices
        for a specific campsite and its parent campground.
        """
        cg_data = self.get_campground_full_info(facility_id)

        site_data = {
            "campsite_id": campsite_id,
            "facility_id": facility_id,
            "campground_name": cg_data.get("facility_name"),
            "campsite_name": f"Site #{campsite_id}",
            "campsite_type": "Standard",
            "loop": "General",
            "image_url": self.get_campground_image(facility_id),
            "alerts": cg_data.get("alerts", []),
            "campground_notices": cg_data.get("notices", []),
            "campsite_notices": [],
            "booking_window": cg_data.get("booking_window", ""),
            "permitted_equipment": [],
            "max_rv_length": 0,
            "max_vehicle_length": 0,
            "site_details": {},
            "equipment_details": {},
            "amenities": []
        }

        try:
            url = f"https://www.recreation.gov/api/camps/campsites/{campsite_id}"
            resp = self.client.get(url, timeout=7.0)
            if resp.status_code == 200:
                cs = resp.json().get("campsite", {})
                site_data["campsite_name"] = cs.get("campsite_name") or f"Site #{campsite_id}"
                site_data["campsite_type"] = cs.get("campsite_type") or "Standard"
                site_data["loop"] = cs.get("loop") or "General"

                # Campsite notices
                for n in cs.get("notices", []):
                    if n.get("notice_text") and not n.get("hide_on_permit", False):
                        site_data["campsite_notices"].append({
                            "type": n.get("notice_type", "info"),
                            "text": n.get("notice_text", "")
                        })

                # Permitted equipment
                rv_types = {"rv", "trailer", "fifth wheel", "popup", "pop up", "motorhome", "truck camper", "caravan"}
                rv_lengths = []
                for eq in cs.get("permitted_equipment", []):
                    if not eq.get("is_deactivated", False):
                        name = eq.get("equipment_name", "")
                        ml = eq.get("max_length")
                        site_data["permitted_equipment"].append({
                            "equipment_name": name,
                            "max_length": ml
                        })
                        if any(t in name.lower() for t in rv_types) and ml is not None:
                            rv_lengths.append(int(ml))
                site_data["max_rv_length"] = max(rv_lengths, default=0)

                # Site details & specs
                site_map = cs.get("site_details_map", {})
                for k, v in site_map.items():
                    name = v.get("attribute_name") or k
                    site_data["site_details"][name] = v.get("attribute_value")

                # Equipment details & specs
                eq_map = cs.get("equipment_details_map", {})
                for k, v in eq_map.items():
                    name = v.get("attribute_name") or k
                    site_data["equipment_details"][name] = v.get("attribute_value")

                mvl = eq_map.get("max_vehicle_length", {}).get("attribute_value")
                if mvl:
                    try:
                        site_data["max_vehicle_length"] = int(mvl)
                    except Exception:
                        pass

                # Amenities
                site_data["amenities"] = [
                    a.get("amenity_name") for a in cs.get("amenities", [])
                    if isinstance(a, dict) and a.get("amenity_name")
                ]
                if not site_data["amenities"] and isinstance(cs.get("amenities"), list):
                    site_data["amenities"] = [str(x) for x in cs.get("amenities") if isinstance(x, str)]

        except Exception as e:
            print(f"[-] Error fetching campsite {campsite_id}: {e}")

        # Real photo
        c_imgs = self.get_campsite_images([campsite_id])
        if c_imgs.get(campsite_id):
            site_data["image_url"] = c_imgs[campsite_id]

        return site_data

    @staticmethod
    def _generate_month_starts(start_dt: datetime, end_dt: datetime) -> List[datetime]:
        """Generate list of 1st-of-month datetimes covering the full range."""
        current = datetime(start_dt.year, start_dt.month, 1)
        end_month = datetime(end_dt.year, end_dt.month, 1)
        months = []
        while current <= end_month:
            months.append(current)
            # Advance to next month
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)
        return months

    def fetch_month_availability(self, facility_id: str, month_start: datetime) -> Dict[str, Any]:
        """Fetch raw monthly availability JSON for a facility."""
        # Format: 2026-09-01T00:00:00.000Z
        iso_str = month_start.strftime("%Y-%m-01T00:00:00.000Z")
        url = self.BASE_AVAILABILITY_URL.format(facility_id=facility_id)
        params = {"start_date": iso_str}

        resp = self.client.get(url, params=params)
        if resp.status_code != 200:
            print(f"[-] Failed to fetch availability for {month_start.strftime('%B %Y')}: HTTP {resp.status_code}")
            return {}
        return resp.json().get("campsites", {})

    def check_availability(
        self,
        facility_id: str,
        start_date_str: str,
        end_date_str: str,
        site_type_filter: Optional[str] = None,
        only_continuous: bool = False,
        min_rv_length: Optional[int] = None
    ) -> Dict[str, pd.DataFrame]:
        """
        Check availability across the specified date range.

        Args:
            facility_id: Campground ID (e.g. '232507')
            start_date_str: Range start (YYYY-MM-DD)
            end_date_str: Range end (YYYY-MM-DD)
            site_type_filter: Optional substring filter for campsite_type (e.g. 'RV', 'TENT', 'ELECTRIC')
            only_continuous: If True, only returns sites available for ALL days in range.
            min_rv_length: Optional minimum RV / trailer length in feet.

        Returns:
            Dict containing:
              - 'detailed': DataFrame with row-per-available-date
              - 'sites_summary': DataFrame aggregated by campsite
        """
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")

        if end_dt < start_dt:
            raise ValueError("end_date must be on or after start_date")

        campground_name = self.get_campground_name(facility_id)
        print(f"\n[+] Checking Availability for: {campground_name} (ID: {facility_id})")
        print(f"[+] Date Range: {start_date_str} to {end_date_str}")
        if site_type_filter:
            print(f"[+] Site Type Filter: '{site_type_filter}'")
        if min_rv_length:
            print(f"[+] Min RV Length Filter: {min_rv_length} ft")

        # Generate list of target dates
        target_dates = []
        curr = start_dt
        while curr <= end_dt:
            target_dates.append(curr.strftime("%Y-%m-%d"))
            curr += timedelta(days=1)

        total_requested_days = len(target_dates)

        # Query all relevant months
        month_starts = self._generate_month_starts(start_dt, end_dt)
        all_campsites_data: Dict[str, Dict[str, Any]] = {}

        for m_start in month_starts:
            month_label = m_start.strftime("%B %Y")
            print(f"[*] Querying availability grid for {month_label}...")
            campsites_month = self.fetch_month_availability(facility_id, m_start)

            for site_id, site_info in campsites_month.items():
                if site_id not in all_campsites_data:
                    all_campsites_data[site_id] = {
                        "campsite_id": site_id,
                        "site_number": site_info.get("site"),
                        "loop": site_info.get("loop"),
                        "campsite_type": site_info.get("campsite_type"),
                        "campsite_reserve_type": site_info.get("campsite_reserve_type"),
                        "min_people": site_info.get("min_num_people"),
                        "max_people": site_info.get("max_num_people"),
                        "availabilities": {}
                    }
                # Merge daily availabilities
                all_campsites_data[site_id]["availabilities"].update(
                    site_info.get("availabilities", {})
                )
            time.sleep(0.2)

        detailed_rows = []
        summary_rows = []

        # Fetch campground photo as default fallback
        campground_img = self.get_campground_image(facility_id)

        # Fetch facility campsite metadata (RV lengths, allowed equipment, etc.)
        site_meta_map = self.get_facility_campsites_metadata(facility_id)

        # Collect unique available site IDs to fetch their real photos
        candidate_site_ids = [
            sid for sid, info in all_campsites_data.items()
            if any(avail_map.get(f"{d}T00:00:00Z") == "Available" for d in target_dates for avail_map in [info.get("availabilities", {})])
        ]
        site_images = self.get_campsite_images(candidate_site_ids)

        for site_id, info in all_campsites_data.items():
            campsite_type = str(info.get("campsite_type") or "")
            if site_type_filter and site_type_filter.upper() not in campsite_type.upper():
                continue

            # Check RV length filter
            meta = site_meta_map.get(str(site_id), {})
            site_max_rv = meta.get("max_rv_length", 0)
            site_max_veh = meta.get("max_vehicle_length", 0)
            site_equip = meta.get("permitted_equipment", [])
            has_rv = meta.get("has_rv_equipment", False)

            if min_rv_length is not None and min_rv_length > 0:
                # Effective RV allowance: maximum of permitted RV length or vehicle length if RV equipment permitted
                effective_rv_capacity = site_max_rv if site_max_rv > 0 else (site_max_veh if has_rv else 0)
                if effective_rv_capacity < min_rv_length:
                    continue

            avail_map = info.get("availabilities", {})
            available_dates_for_site = []

            for date_str in target_dates:
                date_key = f"{date_str}T00:00:00Z"
                status = avail_map.get(date_key, "Unknown")

                if status == "Available":
                    available_dates_for_site.append(date_str)
                    booking_url = f"https://www.recreation.gov/camping/campsites/{site_id}"
                    detailed_rows.append({
                        "campground_name": campground_name,
                        "facility_id": facility_id,
                        "site_id": site_id,
                        "site_number": info.get("site_number"),
                        "loop": info.get("loop"),
                        "campsite_type": campsite_type,
                        "available_date": date_str,
                        "status": "Available",
                        "booking_url": booking_url
                    })

            avail_count = len(available_dates_for_site)
            is_fully_available = (avail_count == total_requested_days)

            if only_continuous and not is_fully_available:
                continue

            if avail_count > 0:
                # Real campsite photo or campground photo fallback
                site_photo = site_images.get(site_id) or campground_img

                summary_rows.append({
                    "campground_name": campground_name,
                    "facility_id": facility_id,
                    "site_id": site_id,
                    "site_number": info.get("site_number"),
                    "loop": info.get("loop"),
                    "campsite_type": campsite_type,
                    "max_rv_length": site_max_rv,
                    "max_vehicle_length": site_max_veh,
                    "permitted_equipment": ", ".join(site_equip) if site_equip else "None specified",
                    "available_days_count": avail_count,
                    "total_requested_days": total_requested_days,
                    "is_continuous_stay": is_fully_available,
                    "available_dates": ", ".join(available_dates_for_site),
                    "image_url": site_photo,
                    "booking_url": f"https://www.recreation.gov/camping/campsites/{site_id}"
                })

        df_detailed = pd.DataFrame(detailed_rows)
        df_summary = pd.DataFrame(summary_rows)

        if not df_summary.empty:
            # Sort by highest number of available days
            df_summary.sort_values(by="available_days_count", ascending=False, inplace=True)

        return {
            "detailed": df_detailed,
            "summary": df_summary
        }


def main():
    parser = argparse.ArgumentParser(description="Check Campsite Date Range Availability on Recreation.gov")
    parser.add_argument("--facility-id", "-f", type=str, default="232507",
                        help="Campground Facility ID (default: 232507 for Assateague Island)")
    parser.add_argument("--start", "-s", type=str, default="2026-09-16",
                        help="Start Date YYYY-MM-DD (default: 2026-09-16)")
    parser.add_argument("--end", "-e", type=str, default="2026-09-30",
                        help="End Date YYYY-MM-DD (default: 2026-09-30)")
    parser.add_argument("--type", "-t", type=str, default=None,
                        help="Optional site type filter (e.g., 'RV', 'TENT', 'ELECTRIC')")
    parser.add_argument("--continuous", "-c", action="store_true",
                        help="Only show sites available for the entire continuous range")

    args = parser.parse_args()

    scraper = CampsiteAvailabilityScraper()
    results = scraper.check_availability(
        facility_id=args.facility_id,
        start_date_str=args.start,
        end_date_str=args.end,
        site_type_filter=args.type,
        only_continuous=args.continuous
    )

    df_summary = results["summary"]
    df_detailed = results["detailed"]

    if not df_summary.empty:
        print("\n=======================================================")
        print(f"  AVAILABLE SITES FOUND: {len(df_summary)} Sites")
        print("=======================================================")
        pd.set_option("display.max_columns", 7)
        pd.set_option("display.width", 1000)

        preview_cols = ["site_number", "loop", "campsite_type", "available_days_count", "is_continuous_stay", "available_dates"]
        print(df_summary[preview_cols].head(15).to_string(index=False))

        # Save files
        summary_csv = f"availability_summary_{args.facility_id}.csv"
        detailed_csv = f"availability_detailed_{args.facility_id}.csv"
        detailed_json = f"availability_detailed_{args.facility_id}.json"

        df_summary.to_csv(summary_csv, index=False, encoding="utf-8")
        df_detailed.to_csv(detailed_csv, index=False, encoding="utf-8")
        df_detailed.to_json(detailed_json, orient="records", indent=2)

        print("\n[OK] Output Files Saved:")
        print(f"  - Summary CSV:  {summary_csv}")
        print(f"  - Detailed CSV: {detailed_csv}")
        print(f"  - Detailed JSON:{detailed_json}")
    else:
        print("\n[-] No available campsites found matching the requested dates and criteria.")


if __name__ == "__main__":
    main()
