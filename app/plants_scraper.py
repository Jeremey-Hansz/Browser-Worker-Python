import os
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

PERENUAL_API_KEY = os.getenv("PERENUAL_API_KEY")
SPRING_BOOT_URL = os.getenv("SPRING_BOOT_URL", "http://localhost:8080")
SCRAPER_TOKEN = os.getenv("SCRAPER_TOKEN")


def normalize_light_level(sunlight: Optional[List[str]]) -> str:
    values = [s.strip().lower() for s in (sunlight or [])]

    joined = " ".join(values)

    if any(x in joined for x in ["full sun", "direct sun", "direct sunlight", "bright direct"]):
        return "high"
    if any(x in joined for x in ["part shade", "partial shade", "filtered", "bright indirect", "indirect"]):
        return "medium"
    return "low"


def normalize_water_freq(watering: Optional[str], benchmark: Optional[Dict[str, Any]]) -> str:
    watering_text = (watering or "").strip().lower()

    if "frequent" in watering_text:
        return "frequent"
    if "average" in watering_text or "moderate" in watering_text:
        return "average"
    if "minimum" in watering_text or "low" in watering_text:
        return "infrequent"

    if benchmark and "value" in benchmark:
        value = benchmark.get("value")
        if isinstance(value, str):
            raw = value.strip()
            if "-" in raw:
                try:
                    start = int(raw.split("-")[0])
                    if start <= 3:
                        return "frequent"
                    if start <= 7:
                        return "average"
                    return "infrequent"
                except ValueError:
                    pass
        elif isinstance(value, (int, float)):
            if value <= 3:
                return "frequent"
            if value <= 7:
                return "average"
            return "infrequent"

    return "average"


def normalize_difficulty(care_level: Optional[str], maintenance: Optional[str]) -> str:
    care = (care_level or "").strip().lower()
    maint = (maintenance or "").strip().lower()

    if care in ["easy", "low"] or maint == "low":
        return "beginner"
    if care in ["medium", "moderate"] or maint == "medium":
        return "intermediate"
    if care in ["high", "difficult"] or maint == "high":
        return "advanced"

    return "intermediate"


def normalize_category(raw: Dict[str, Any]) -> str:
    if raw.get("indoor") is True:
        return "INDOOR-PLANTS"

    plant_type = (raw.get("type") or "").strip().lower()

    if plant_type in ["herb"]:
        return "HERBS"
    if plant_type in ["vegetable"]:
        return "VEGETABLES"
    if plant_type in ["tree"] and raw.get("edible_fruit"):
        return "FRUIT-TREES"
    if plant_type in ["succulent"]:
        return "SUCCULENTS"
    if plant_type in ["flower"]:
        return "FLOWERS"

    return "OUTDOOR-PLANTS"


def pick_image_url(default_image: Optional[Dict[str, Any]]) -> str:
    img = default_image or {}
    return (
        img.get("regular_url")
        or img.get("medium_url")
        or img.get("small_url")
        or img.get("thumbnail")
        or ""
    )


def map_perenual_to_plant_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    scientific_names = raw.get("scientific_name") or []
    scientific_name = scientific_names[0].strip() if scientific_names else ""

    pet_safe = not bool(raw.get("poisonous_to_pets", False))
    indoor = bool(raw.get("indoor", False))

    care_needs = {
        "light_needs": normalize_light_level(raw.get("sunlight")),
        "water_freq": normalize_water_freq(
            raw.get("watering"),
            raw.get("watering_general_benchmark"),
        ),
        "difficulty": normalize_difficulty(
            raw.get("care_level"),
            raw.get("maintenance"),
        ),
        "growth_rate": (raw.get("growth_rate") or "").strip().lower(),
        "soil": [str(s).strip().lower() for s in (raw.get("soil") or [])],
        "space_type": "indoor" if indoor else "outdoor",
        "pet_safe": pet_safe,
    }

    return {
        "scientificName": scientific_name,
        "commonName": raw.get("common_name"),
        "description": raw.get("description"),
        "category": normalize_category(raw),
        "careNeeds": care_needs,
        "imageUrl": pick_image_url(raw.get("default_image")),
        "petSafe": pet_safe,
        "indoor": indoor,
    }


def fetch_species_list_page(page: int = 1) -> Dict[str, Any]:
    url = "https://perenual.com/api/species-list"
    response = requests.get(
        url,
        params={
            "key": PERENUAL_API_KEY,
            "page": page,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_species_details(species_id: int) -> Dict[str, Any]:
    url = f"https://perenual.com/api/v2/species/details/{species_id}"
    response = requests.get(
        url,
        params={"key": PERENUAL_API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def post_plants_to_backend(plants: List[Dict[str, Any]]) -> Dict[str, Any]:
    url = f"{SPRING_BOOT_URL}/api/plants/bulk-import"

    headers = {"Content-Type": "application/json"}
    if SCRAPER_TOKEN:
        headers["Authorization"] = f"Bearer {SCRAPER_TOKEN}"

    response = requests.post(
        url,
        json={"plants": plants},
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def run_plant_import(max_pages: int = 1, sleep_seconds: float = 0.5) -> Dict[str, Any]:
    if not PERENUAL_API_KEY:
        raise ValueError("Missing PERENUAL_API_KEY")

    imported = 0
    failed = 0
    errors = []

    for page in range(1, max_pages + 1):
        list_payload = fetch_species_list_page(page)
        species_items = list_payload.get("data", [])

        batch = []

        for item in species_items:
            species_id = item.get("id")
            if not species_id:
                continue

            try:
                details = fetch_species_details(species_id)
                plant_payload = map_perenual_to_plant_payload(details)

                if not plant_payload["scientificName"]:
                    failed += 1
                    errors.append({"id": species_id, "message": "Missing scientific name"})
                    continue

                batch.append(plant_payload)
            except Exception as e:
                failed += 1
                errors.append({"id": species_id, "message": str(e)})

            time.sleep(sleep_seconds)

        if batch:
            post_plants_to_backend(batch)
            imported += len(batch)

    return {
        "success": failed == 0,
        "imported": imported,
        "failed": failed,
        "errors": errors,
    }


if __name__ == "__main__":
    result = run_plant_import(max_pages=1)
    print(result)