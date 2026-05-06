import os
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import uvicorn

from app.extract_products import run_extract_products

load_dotenv()

SCRAPER_TOKEN = os.getenv("SCRAPER_TOKEN")

app = FastAPI(title="PlantHub Scraper Service")


class ScrapeRequest(BaseModel):
    shopCode: str = "PLNTS_NL"
    categoryUrls: Optional[List[HttpUrl]] = None
    token: Optional[str] = None


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
    if SCRAPER_TOKEN and req.token != SCRAPER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    category_urls = (
        [str(u) for u in req.categoryUrls]
        if req.categoryUrls
        else ["https://plnts.com/nl/shop/all-plnts"]
    )

    try:
        result = run_extract_products(
            category_urls=category_urls,
            shop_code=req.shopCode,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape failed: {e}")

    return ScrapeResponse(
        shopCode=result["shopCode"],
        success=result["success"],
        totalFound=result["totalFound"],
        successCount=result["successCount"],
        failCount=result["failCount"],
        products=result["products"],
        errors=result["errors"],
    )


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)