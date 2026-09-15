"""
Recreation.gov Campground & Campsite Availability Web Application
================================================================
FastAPI backend powering the search and live date range availability UI.
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Optional, List, Dict
import os

from recreation_gov_scraper import RecreationGovScraper
from campsite_availability_scraper import CampsiteAvailabilityScraper

app = FastAPI(title="Recreation.gov Campsite Availability Finder")

# Setup templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)

# Initialize scrapers
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


@app.get("/", response_class=HTMLResponse)
def serve_home(request: Request):
    """Render the main index.html user interface."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"states": US_STATES}
    )


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
    OPTION A: Check Date Range Availability for all campsites in a specific campground.
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)