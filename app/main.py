import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from dotenv import load_dotenv

from app.extract_products import run_plnts_scrape

load_dotenv()

app = FastAPI(title="PlantHub Scraper Service")


class ScrapeRequest(BaseModel):
    shopCode: str = "PLNTS_NL"
    categoryUrls: Optional[List[HttpUrl]] = None


class ScrapeError(BaseModel):
    url: str
    message: str


class ScrapeResponse(BaseModel):
    shopCode: str
    success: bool
    totalFound: int
    successCount: int
    failCount: int
    products: List[dict]
    errors: List[ScrapeError]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/scrape/plnts", response_model=ScrapeResponse)
def scrape_plnts(req: ScrapeRequest):
    category_urls = (
        [str(u) for u in req.categoryUrls]
        if req.categoryUrls
        else ["https://plnts.com/nl/shop/all-plnts"]
    )

    try:
        result = run_plnts_scrape(category_urls)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape failed: {e}")

    return ScrapeResponse(
        shopCode=req.shopCode,
        success=True,
        totalFound=result["totalFound"],
        successCount=result["successCount"],
        failCount=result["failCount"],
        products=result["products"],
        errors=result["errors"],
    )