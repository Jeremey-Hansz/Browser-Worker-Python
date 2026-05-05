import os
import re
from typing import List
from urllib.parse import urljoin, urlparse, urlunparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

PROXY_SERVER = os.getenv("PROXY_SERVER")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")


def build_proxy():
    if not PROXY_SERVER:
        return None

    proxy = {
        "server": PROXY_SERVER
    }

    if PROXY_USERNAME:
        proxy["username"] = PROXY_USERNAME

    if PROXY_PASSWORD:
        proxy["password"] = PROXY_PASSWORD

    return proxy

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


def build_proxy():
    server = os.getenv("PROXY_SERVER")
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")

    if not server:
        return None

    proxy = {"server": server}

    if username:
        proxy["username"] = username

    if password:
        proxy["password"] = password

    return proxy


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

    name = "Unknown"
    try:
        name = page.locator("h1").first.text_content(timeout=3000).strip()
    except Exception:
        pass

    price_text = ""
    try:
        price_text = (
            page.locator("h1")
            .first
            .locator('xpath=following::section[1]//div[contains(@class,"flex-row") and contains(@class,"gap-2")]//span[1]')
            .first
            .text_content(timeout=3000)
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


def run_plnts_scrape(category_urls: List[str]) -> dict:
    products = []
    errors = []
    success_count = 0
    fail_count = 0

    with sync_playwright() as p:
        proxy = build_proxy()

        browser = p.chromium.launch(
            headless=True,
            proxy=proxy
        )

        page = browser.new_page()
        all_urls = set()

        for category_url in category_urls:
            print(f"Loading category: {category_url}")
            page.goto(category_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            print("Current URL:", page.url)
            print("Page title:", page.title())

            all_hrefs = page.locator("a").evaluate_all("""
                elements => elements
                    .map(el => el.getAttribute("href"))
                    .filter(Boolean)
            """)

            print("Total anchors:", len(all_hrefs))
            print("Sample hrefs:", all_hrefs[:30])

            for _ in range(5):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1500)

            links = extract_links(page, category_url)
            print(f"Found {len(links)} links on {category_url}")
            print("Sample product links:", links[:20])

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
    result = run_plnts_scrape(CATEGORY_URLS)
    print(result)


if __name__ == "__main__":
    run()