"""
Recreation.gov Direct REST API Scraper
======================================
Fast, lightweight scraper that fetches campsite and campground search data
directly from Recreation.gov's internal API endpoints without needing a browser.

Features:
- Direct JSON API (ultra-fast, zero browser overhead)
- Auto-pagination support
- Formatted pricing, location, ratings & direct booking URLs
- Exports clean structured data to Pandas DataFrame, CSV, and JSON
"""

import time
from typing import Any, Dict, List, Optional
import httpx
import pandas as pd


class RecreationGovScraper:
    """Scraper client for Recreation.gov search and facility APIs."""

    BASE_SEARCH_GEO_URL = "https://www.recreation.gov/api/search/geo"

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

    def __init__(self, timeout: float = 20.0):
        self.client = httpx.Client(
            headers=self.DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True
        )

    def _format_price(self, price_range: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Parses the price_range dict into structured min/max and string representation."""
        if not price_range or not isinstance(price_range, dict):
            return {
                "min_price": None,
                "max_price": None,
                "price_per_unit": None,
                "formatted_price": "N/A",
            }

        min_val = price_range.get("amount_min")
        max_val = price_range.get("amount_max")
        per_unit = price_range.get("per_unit", "Night")

        if min_val is not None and max_val is not None:
            if min_val == max_val:
                formatted = f"${min_val} / {per_unit}"
            else:
                formatted = f"${min_val} - ${max_val} / {per_unit}"
        elif min_val is not None:
            formatted = f"${min_val}+ / {per_unit}"
        else:
            formatted = "N/A"

        return {
            "min_price": min_val,
            "max_price": max_val,
            "price_per_unit": per_unit,
            "formatted_price": formatted,
        }

    def scrape_campgrounds(
        self,
        query: str = "Maryland",
        radius: int = 165,
        campsite_type: str = "rv/ motorhome/ trailer",
        vehicle_length: Optional[int] = None,
        sort: str = "available",
        page_size: int = 50,
    ) -> pd.DataFrame:
        """
        Scrapes all campgrounds matching query and filters.
        
        Args:
            query: Search query (state, park name, or city)
            radius: Radius in miles
            campsite_type: Filter for site type (e.g. 'rv/ motorhome/ trailer')
            vehicle_length: Optional vehicle/RV length in feet (e.g. 30)
            sort: Sort order ('available', 'best_match', etc.)
            page_size: Items per page request (default 50)
            
        Returns:
            pd.DataFrame containing all parsed campground records.
        """
        all_results: List[Dict[str, Any]] = []
        start_offset = 0
        total_items = None

        print(f"\n[+] Scraping Recreation.gov for: '{query}'")
        print(f"[+] Filters: Campsite Type='{campsite_type}', Vehicle Length={vehicle_length}, Radius={radius}mi, Sort='{sort}'")

        # Map filter categories to Recreation.gov API params
        fg_filters = ["camping"]
        if "rv" in campsite_type.lower():
            fg_filters.append("rmt")  # rmt = RV / Motorhome / Trailer
        if vehicle_length is not None and vehicle_length > 0:
            fg_filters.append(f"vehicle-length:{vehicle_length}")

        while True:
            params = {
                "q": query,
                "radius": str(radius),
                "size": str(page_size),
                "start": str(start_offset),
                "sort": sort,
                "fq": "-entity_type:(tour OR timedentry_tour)",
                "fg": fg_filters,
            }

            try:
                response = self.client.get(self.BASE_SEARCH_GEO_URL, params=params)
                if response.status_code != 200:
                    print(f"[-] Request failed with status code: {response.status_code}")
                    break

                data = response.json()
                total_items = data.get("total", 0)
                items = data.get("results", [])

                if not items:
                    print("[*] No more results found.")
                    break

                for item in items:
                    entity_id = item.get("entity_id") or item.get("id")
                    campground_url = (
                        f"https://www.recreation.gov/camping/campgrounds/{entity_id}"
                        if entity_id else ""
                    )

                    price_info = self._format_price(item.get("price_range"))

                    all_results.append({
                        "facility_id": entity_id,
                        "name": item.get("name"),
                        "city": item.get("city"),
                        "state": item.get("state_code"),
                        "price": price_info["formatted_price"],
                        "min_price": price_info["min_price"],
                        "max_price": price_info["max_price"],
                        "average_rating": round(item.get("average_rating", 0) or 0, 2),
                        "ratings_count": item.get("number_of_ratings"),
                        "total_campsites": item.get("campsites_count"),
                        "accessible_campsites": item.get("accessible_campsites_count"),
                        "reservable": item.get("reservable"),
                        "parent_name": item.get("parent_name"),
                        "latitude": item.get("latitude"),
                        "longitude": item.get("longitude"),
                        "url": campground_url,
                        "preview_image_url": item.get("preview_image_url"),
                    })

                print(f"[+] Fetched {len(all_results)} of {total_items} campgrounds...")

                start_offset += len(items)
                if start_offset >= total_items:
                    break

                time.sleep(0.3)  # Gentle delay between pagination calls

            except Exception as exc:
                print(f"[-] Error during request: {exc}")
                break

        df = pd.DataFrame(all_results)
        print(f"\n[OK] Scraping complete! Total campgrounds extracted: {len(df)}")
        return df


def main():
    scraper = RecreationGovScraper()
    
    # Run scraper for Maryland RV / Trailer campgrounds
    df = scraper.scrape_campgrounds(
        query="Maryland",
        radius=165,
        campsite_type="rv/ motorhome/ trailer",
        sort="available"
    )

    if not df.empty:
        # Display preview in console
        pd.set_option("display.max_columns", 8)
        pd.set_option("display.width", 1000)
        print("\n--- Preview Top 10 Campgrounds ---")
        preview_cols = ["name", "city", "state", "price", "average_rating", "total_campsites"]
        print(df[preview_cols].head(10).to_string(index=False))

        # Export outputs
        csv_filename = "recreation_gov_campsites.csv"
        json_filename = "recreation_gov_campsites.json"

        df.to_csv(csv_filename, index=False, encoding="utf-8")
        df.to_json(json_filename, orient="records", indent=2)

        print(f"\n[OK] CSV file created: {csv_filename}")
        print(f"[OK] JSON file created: {json_filename}")
    else:
        print("[-] No records were scraped.")


if __name__ == "__main__":
    main()
