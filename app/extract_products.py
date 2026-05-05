import os
import re
import json
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from typing import List

load_dotenv()

WEBHOOK_URL = os.getenv("BACKEND_WEBHOOK_URL", "https://planthub-backend-psfe.onrender.com/webhooks/scraper/products")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SCRAPER_SECRET")

CATEGORY_URLS = [
    "https://plnts.com/nl/shop/all-plnts",
]

PRODUCT_LINK_SELECTORS = [
    'a[href*="/product/"]',
    'a[href*="/nl/product/"]',
    'a:has(img)',
    'a',
    '[class*="product"] a',
    '[class*="product-card"] a',
    '[class*="item"] a',
]

SIZE_LABELS = {
    "s": "Small",
    "m": "Medium",
    "l": "Large",
    "xl": "XL",
    "xxl": "XXL",
    "xxxl": "XXXL",
    "default": "Default",
}


def normalize_url(href: str, base_url: str) -> str | None:
    try:
        u = urlparse(urljoin(base_url, href))
        u = u._replace(fragment="", query="")
        return urlunparse(u)
    except Exception:
        return None


def parse_product_url_parts(url: str):
    try:
        u = urlparse(url)
        path = u.path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        slug = parts[-1] if parts else ""
        match = re.search(r"-(xxxl|xxl|xl|l|m|s)$", slug, re.IGNORECASE)
        size_key = match.group(1).lower() if match else "default"
        family_slug = re.sub(r"-(xxxl|xxl|xl|l|m|s)$", "", slug, flags=re.IGNORECASE)
        return {
            "origin": f"{u.scheme}://{u.netloc}",
            "parts": parts,
            "slug": slug,
            "familySlug": family_slug,
            "sizeKey": size_key,
        }
    except Exception:
        return None


def derive_product_family_url(url: str) -> str:
    parsed = parse_product_url_parts(url)
    if not parsed:
        return url
    parts = parsed["parts"][:]
    parts[-1] = parsed["familySlug"]
    return f"{parsed['origin']}/{'/'.join(parts)}"


def extract_size_key(url: str) -> str:
    parsed = parse_product_url_parts(url)
    return parsed["sizeKey"] if parsed else "default"


def to_size_label(size_key: str) -> str:
    return SIZE_LABELS.get(size_key, size_key.upper())


def is_valid_product_url(url: str) -> bool:
    return bool(
        url
        and "plnts.com" in url
        and "/product/" in url
        and "/cart" not in url
        and "/checkout" not in url
        and "/account" not in url
        and "/login" not in url
        and "/wishlist" not in url
    )


def extract_links(page, base_url: str):
    found = set()
    for selector in PRODUCT_LINK_SELECTORS:
        try:
            items = page.locator(selector).evaluate_all("""
                elements => elements.map(el => ({
                    text: (el.textContent || "").replace(/\\s+/g, " ").trim(),
                    href: el.getAttribute("href")
                }))
            """)
        except Exception:
            items = []

        for item in items:
            href = item.get("href")
            if not href:
                continue
            absolute = normalize_url(href, base_url)
            if is_valid_product_url(absolute):
                found.add(absolute)
    return list(found)


def extract_price(text: str) -> float:
    m = re.search(r"€?\s*([\d.,]+)", text or "")
    if not m:
        return 0.0
    return float(m.group(1).replace(",", "."))


def extract_product_data(page, url: str):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    family_url = derive_product_family_url(url)
    size_key = extract_size_key(url)
    size_label = to_size_label(size_key)

    name = page.locator("h1").first.text_content(timeout=2000).strip()
    price_text = ""
    try:
        price_text = (
            page.locator("h1")
            .first
            .locator('xpath=following::section[1]//div[contains(@class,"flex-row") and contains(@class,"gap-2")]//span[1]')
            .first
            .text_content(timeout=2000)
            .strip()
        )
    except Exception:
        pass

    min_price = extract_price(price_text)

    image = ""
    try:
        image = page.locator('img[src*="plnts.com"]').first.get_attribute("src") or ""
    except Exception:
        pass

    return {
        "shopId": 1,
        "name": name or "Unknown",
        "canonicalUrl": family_url,
        "sourceUrl": url,
        "affiliateUrl": None,
        "minPrice": min_price,
        "defaultCurrency": "EUR",
        "imageUrl": image,
        "regionalData": {
            "NL": {
                "currency": "EUR",
                "delivery": True,
                "variants": {
                    size_key: {
                        "label": size_label,
                        "price": min_price,
                        "inStock": True,
                    }
                }
            }
        },
        "data": {
            "tags": ["indoor", "green"],
            "category": "plants",
        },
        "contentHash": None,
    }


def post_to_webhook(product: dict) -> bool:
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": WEBHOOK_SECRET,
    }
    r = requests.post(WEBHOOK_URL, headers=headers, data=json.dumps(product), timeout=30)
    if r.ok:
        print(f"✅ Saved: {product['name']} (€{product['minPrice']})")
        print("Response:", r.text)
        return True
    print(f"❌ Failed {product['canonicalUrl']}: {r.status_code}")
    print("Response body:", r.text)
    return False

def run_extract_products(category_urls: List[str]) -> dict:
    products = []
    errors = []
    success_count = 0
    fail_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        all_urls = set()

        for category_url in category_urls:
            page.goto(category_url, wait_until="networkidle", timeout=60000)
            page.wait_for_selector('a[href*="/product/"]', timeout=10000)
            links = extract_links(page, category_url)
            for url in links:
                all_urls.add(url)

        for url in sorted(all_urls):
            try:
                product = extract_product_data(page, url)
                products.append(product)
                success_count += 1
            except Exception as e:
                errors.append({"url": url, "message": str(e)})
                fail_count += 1

        browser.close()

    return {
        "totalFound": len(all_urls),
        "successCount": success_count,
        "failCount": fail_count,
        "products": products,
        "errors": errors,
    }


def run():
    print("SCRIPT STARTED")
    print("SECRET LOADED?", bool(WEBHOOK_SECRET))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        all_urls = set()

        for category_url in CATEGORY_URLS:
            print(f"\n📁 Crawling category: {category_url}")
            page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            links = extract_links(page, category_url)
            for url in links:
                all_urls.add(url)

            print(f"Found {len(links)} products in this category")

        print(f"\n🚀 Found {len(all_urls)} total unique product URLs")

        success_count = 0
        fail_count = 0

        for i, url in enumerate(sorted(all_urls), start=1):
            print(f"\n[{i}/{len(all_urls)}] Processing: {url}")
            try:
                product = extract_product_data(page, url)
                if post_to_webhook(product):
                    success_count += 1
                else:
                    fail_count += 1
                time.sleep(0.5)
            except Exception as e:
                print(f"❌ Failed to extract {url}: {e}")
                fail_count += 1

        print(f"\n🎉 Done! Success: {success_count}, Failed: {fail_count}")
        browser.close()


if __name__ == "__main__":
    run()