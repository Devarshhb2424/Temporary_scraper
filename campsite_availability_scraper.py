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
        only_continuous: bool = False
    ) -> Dict[str, pd.DataFrame]:
        """
        Check availability across the specified date range.

        Args:
            facility_id: Campground ID (e.g. '232507')
            start_date_str: Range start (YYYY-MM-DD)
            end_date_str: Range end (YYYY-MM-DD)
            site_type_filter: Optional substring filter for campsite_type (e.g. 'RV', 'TENT', 'ELECTRIC')
            only_continuous: If True, only returns sites available for ALL days in range.

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
