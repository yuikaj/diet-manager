"""
4-tier nutrition lookup:
  1. SQLite cache (nutrition_cache table)
  2. local_nutrition.json (hand-curated fallback)
  3. USDA FoodData Central API
  4. None (unknown — caller decides how to handle)
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from config import DATA_DIR, USDA_API_KEY
from db.nutrition import get_cached, save_to_cache

_TRANSLATIONS_PATH = DATA_DIR / "ingredient_translations.json"
_LOCAL_PATH = DATA_DIR / "local_nutrition.json"

_translations: Optional[dict] = None
_local_db: Optional[dict] = None

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# USDA nutrient IDs we care about.
# SR Legacy foods use 1008 for energy; Foundation Foods use 2047/2048.
# Priority for kcal: 1008 > 2047 > 2048
_KCAL_IDS = (1008, 2047, 2048)
_NUTRIENT_ID_MAP = {
    1003: "protein",
    1004: "fat",
    1005: "carbs",
    1093: "sodium",
    1079: "fiber",
    1162: "vitc",
    1089: "iron",
    1087: "calcium",
    1092: "potassium",
    1110: "vitd",
    1106: "vita",
    1090: "magnesium",
    1095: "zinc",
}

# Unit → grams conversion (approx)
UNIT_TO_G: dict = {
    "g": 1.0,
    "克": 1.0,
    "ml": 1.0,
    "毫升": 1.0,
    "kg": 1000.0,
    "千克": 1000.0,
    "汤匙": 15.0,
    "大匙": 15.0,
    "茶匙": 5.0,
    "小匙": 5.0,
    "勺": 10.0,
    "杯": 240.0,
    "片": 20.0,        # rough estimate per slice
    "个": 100.0,       # generic piece ≈ 100g
    "颗": 5.0,
    "根": 80.0,
    "条": 100.0,
    "块": 50.0,
    "把": 100.0,
    "斤": 500.0,
    "两": 50.0,
    "适量": None,      # unquantifiable
    "少许": None,
}


@dataclass
class NutritionPer100g:
    kcal: Optional[float] = None
    protein: Optional[float] = None
    fat: Optional[float] = None
    carbs: Optional[float] = None
    sodium: Optional[float] = None
    fiber: Optional[float] = None
    vitc: Optional[float] = None
    iron: Optional[float] = None
    calcium: Optional[float] = None
    potassium: Optional[float] = None
    vitd: Optional[float] = None
    vita: Optional[float] = None
    magnesium: Optional[float] = None
    zinc: Optional[float] = None
    source: str = "unknown"
    food_name: str = ""
    usda_url: str = ""


@dataclass
class MealNutrition:
    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbs: float = 0.0
    sodium: float = 0.0
    fiber: float = 0.0
    vitc: float = 0.0
    iron: float = 0.0
    calcium: float = 0.0
    potassium: float = 0.0
    vitd: float = 0.0
    vita: float = 0.0
    magnesium: float = 0.0
    zinc: float = 0.0
    found: int = 0
    missing: list = field(default_factory=list)


def _load_translations() -> dict:
    global _translations
    if _translations is None:
        try:
            with open(_TRANSLATIONS_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _translations = {k: v for k, v in raw.items() if not k.startswith("_")}
        except FileNotFoundError:
            _translations = {}
    return _translations


def _load_local() -> dict:
    global _local_db
    if _local_db is None:
        try:
            with open(_LOCAL_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _local_db = {k: v for k, v in raw.items() if not k.startswith("_")}
        except FileNotFoundError:
            _local_db = {}
    return _local_db


def _row_to_nutrition(row: dict) -> NutritionPer100g:
    fdc_id = row.get("usda_food_id", "")
    usda_url = (
        f"https://fdc.nal.usda.gov/food-details/{fdc_id}/nutrients"
        if fdc_id and str(fdc_id).isdigit()
        else ""
    )
    return NutritionPer100g(
        kcal=row.get("kcal_per_100g"),
        protein=row.get("protein_per_100g"),
        fat=row.get("fat_per_100g"),
        carbs=row.get("carbs_per_100g"),
        sodium=row.get("sodium_per_100g"),
        fiber=row.get("fiber_per_100g"),
        vitc=row.get("vitc_per_100g"),
        iron=row.get("iron_per_100g"),
        calcium=row.get("calcium_per_100g"),
        potassium=row.get("potassium_per_100g"),
        vitd=row.get("vitd_per_100g"),
        vita=row.get("vita_per_100g"),
        magnesium=row.get("magnesium_per_100g"),
        zinc=row.get("zinc_per_100g"),
        source=row.get("source", "cache"),
        food_name=row.get("en_name") or row.get("ingredient_name", ""),
        usda_url=usda_url,
    )


def _fetch_usda(en_query: str) -> Optional[tuple[str, str, dict]]:
    """Query USDA API. Returns (food_id, description, nutrients_dict) or None."""
    if not USDA_API_KEY:
        return None
    try:
        resp = requests.get(
            USDA_SEARCH_URL,
            params={
                "query": en_query,
                "api_key": USDA_API_KEY,
                "pageSize": 5,
                "dataType": "Foundation,SR Legacy",
            },
            timeout=8,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    data = resp.json()
    foods = data.get("foods", [])
    if not foods:
        return None

    food = foods[0]
    food_id = str(food.get("fdcId", "usda_unknown"))
    description = food.get("description", en_query)

    # Collect nutrients: handle SR Legacy (1008) vs Foundation (2047/2048) energy IDs
    kcal_candidates: dict = {}
    nutrients: dict = {}
    for nutrient in food.get("foodNutrients", []):
        nid = nutrient.get("nutrientId")
        val = nutrient.get("value")
        if val is None:
            continue
        if nid in _KCAL_IDS:
            kcal_candidates[nid] = float(val)
        elif nid in _NUTRIENT_ID_MAP:
            nutrients[_NUTRIENT_ID_MAP[nid]] = float(val)

    # Pick kcal with priority: 1008 > 2047 > 2048
    for kid in _KCAL_IDS:
        if kid in kcal_candidates:
            nutrients["kcal"] = kcal_candidates[kid]
            break

    return food_id, description, nutrients


def lookup_ingredient(
    name: str, force_refresh: bool = False
) -> Optional[NutritionPer100g]:
    """
    Return NutritionPer100g for `name` using 4-tier lookup.
    Returns None if all tiers fail.
    """
    # Tier 1: SQLite cache
    if not force_refresh:
        row = get_cached(name)
        if row:
            return _row_to_nutrition(row)

    # Tier 2: local_nutrition.json
    local = _load_local()
    if name in local:
        entry = local[name]
        p = entry["per_100g"]
        n = NutritionPer100g(
            kcal=p.get("kcal"),
            protein=p.get("protein"),
            fat=p.get("fat"),
            carbs=p.get("carbs"),
            sodium=p.get("sodium"),
            fiber=p.get("fiber"),
            vitc=p.get("vitc"),
            iron=p.get("iron"),
            calcium=p.get("calcium"),
            potassium=p.get("potassium"),
            source=entry.get("source") or "local",
            food_name=entry.get("en_name", name),
        )
        save_to_cache(
            ingredient_name=name,
            en_name=entry.get("en_name"),
            usda_food_id=f"local_{name}",
            nutrients={
                "kcal": n.kcal, "protein": n.protein, "fat": n.fat,
                "carbs": n.carbs, "sodium": n.sodium, "fiber": n.fiber,
                "vitc": n.vitc, "iron": n.iron, "calcium": n.calcium,
                "potassium": n.potassium,
            },
            source=entry.get("source") or "local",
        )
        return n

    # Tier 3: USDA API
    translations = _load_translations()
    en_query = translations.get(name, name)  # fallback: use Chinese name directly
    result = _fetch_usda(en_query)
    if result:
        food_id, description, nutrients = result
        n = NutritionPer100g(
            kcal=nutrients.get("kcal"),
            protein=nutrients.get("protein"),
            fat=nutrients.get("fat"),
            carbs=nutrients.get("carbs"),
            sodium=nutrients.get("sodium"),
            fiber=nutrients.get("fiber"),
            vitc=nutrients.get("vitc"),
            iron=nutrients.get("iron"),
            calcium=nutrients.get("calcium"),
            potassium=nutrients.get("potassium"),
            vitd=nutrients.get("vitd"),
            vita=nutrients.get("vita"),
            magnesium=nutrients.get("magnesium"),
            zinc=nutrients.get("zinc"),
            source="usda",
            food_name=description,
            usda_url=f"https://fdc.nal.usda.gov/food-details/{food_id}/nutrients",
        )
        save_to_cache(
            ingredient_name=name,
            en_name=description,
            usda_food_id=food_id,
            nutrients=nutrients,
            source="usda",
        )
        return n

    # Tier 4: not found
    return None


def to_grams(amount: Optional[float], unit: str) -> Optional[float]:
    """Convert amount+unit to grams. Returns None for unquantifiable units."""
    if amount is None:
        return None
    factor = UNIT_TO_G.get(unit)
    if factor is None:
        return None
    return amount * factor


def calc_nutrition(ingredients: list[dict]) -> MealNutrition:
    """
    Calculate total nutrition for a list of ingredient dicts.
    Each dict must have keys: name (str), amount (float|None), unit (str).
    Returns MealNutrition with per-meal totals.
    """
    result = MealNutrition()

    for ing in ingredients:
        name = ing.get("name", "").strip()
        if not name:
            continue

        grams = to_grams(ing.get("amount"), ing.get("unit", "g"))
        if grams is None or grams <= 0:
            result.missing.append(name)
            continue

        n = lookup_ingredient(name)
        if n is None:
            result.missing.append(name)
            continue

        intake_ratio = float(ing.get("intake_ratio", 1.0))
        scale = grams / 100.0 * intake_ratio
        result.kcal      += (n.kcal      or 0) * scale
        result.protein   += (n.protein   or 0) * scale
        result.fat       += (n.fat       or 0) * scale
        result.carbs     += (n.carbs     or 0) * scale
        result.sodium    += (n.sodium    or 0) * scale
        result.fiber     += (n.fiber     or 0) * scale
        result.vitc      += (n.vitc      or 0) * scale
        result.iron      += (n.iron      or 0) * scale
        result.calcium   += (n.calcium   or 0) * scale
        result.potassium += (n.potassium or 0) * scale
        result.vitd      += (n.vitd      or 0) * scale
        result.vita      += (n.vita      or 0) * scale
        result.magnesium += (n.magnesium or 0) * scale
        result.zinc      += (n.zinc      or 0) * scale
        result.found += 1

    return result


def calc_nutrition_with_breakdown(ingredients: list) -> tuple:
    """
    Like calc_nutrition but also returns per-ingredient breakdown rows for display.
    Returns (MealNutrition, list[dict]).
    Each dict: 食材, 用量, 热量, 蛋白质, 脂肪, 碳水, 钠, source
    """
    total = MealNutrition()
    rows  = []

    for ing in ingredients:
        name = (ing.get("name") or "").strip()
        if not name:
            continue
        raw_amt = ing.get("amount")
        unit    = ing.get("unit", "g")
        grams   = to_grams(raw_amt, unit)
        ir      = float(ing.get("intake_ratio", 1.0))

        if grams is None or grams <= 0:
            total.missing.append(name)
            rows.append({"食材": name, "用量": f"{raw_amt} {unit}", "热量": "—",
                         "蛋白质": "—", "脂肪": "—", "碳水": "—", "钠": "—",
                         "来源": "⚠️ 无法换算", "USDA": ""})
            continue

        n = lookup_ingredient(name)
        if n is None:
            total.missing.append(name)
            rows.append({"食材": name, "用量": f"{grams:.0f}g", "热量": "—",
                         "蛋白质": "—", "脂肪": "—", "碳水": "—", "钠": "—",
                         "来源": "❌ 未找到", "USDA": ""})
            continue

        scale = grams / 100.0 * ir
        total.kcal      += (n.kcal      or 0) * scale
        total.protein   += (n.protein   or 0) * scale
        total.fat       += (n.fat       or 0) * scale
        total.carbs     += (n.carbs     or 0) * scale
        total.sodium    += (n.sodium    or 0) * scale
        total.fiber     += (n.fiber     or 0) * scale
        total.vitc      += (n.vitc      or 0) * scale
        total.iron      += (n.iron      or 0) * scale
        total.calcium   += (n.calcium   or 0) * scale
        total.potassium += (n.potassium or 0) * scale
        total.vitd      += (n.vitd      or 0) * scale
        total.vita      += (n.vita      or 0) * scale
        total.magnesium += (n.magnesium or 0) * scale
        total.zinc      += (n.zinc      or 0) * scale
        total.found += 1

        ir_label = f" ×{ir:.0%}" if ir < 1.0 else ""
        rows.append({
            "食材":  name,
            "用量":  f"{grams:.0f}g{ir_label}",
            "热量":  f"{(n.kcal or 0)*scale:.0f} kcal",
            "蛋白质": f"{(n.protein or 0)*scale:.1f}g",
            "脂肪":  f"{(n.fat or 0)*scale:.1f}g",
            "碳水":  f"{(n.carbs or 0)*scale:.1f}g",
            "钠":    f"{(n.sodium or 0)*scale:.0f}mg",
            "来源":  n.source,
            "USDA": n.usda_url,
        })

    return total, rows
