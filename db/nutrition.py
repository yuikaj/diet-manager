from typing import Optional
from db.init_db import get_connection

_FIELDS = (
    "kcal_per_100g", "protein_per_100g", "fat_per_100g", "carbs_per_100g",
    "sodium_per_100g", "fiber_per_100g", "vitc_per_100g", "iron_per_100g",
    "calcium_per_100g", "potassium_per_100g",
    "vitd_per_100g", "vita_per_100g", "magnesium_per_100g", "zinc_per_100g",
)


def get_cached(ingredient_name: str) -> Optional[dict]:
    """Return first matching nutrition_cache row as dict, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nutrition_cache WHERE ingredient_name = ? LIMIT 1",
            (ingredient_name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_to_cache(
    ingredient_name: str,
    en_name: Optional[str],
    usda_food_id: str,
    nutrients: dict,
    source: str = "usda",
) -> None:
    """Upsert a row into nutrition_cache (delete-then-insert to handle PK).

    Clears both ingredient_name AND usda_food_id conflicts so multiple Chinese
    names that map to the same USDA food ID don't cause a UNIQUE violation.
    """
    source = source or "local"   # guard against None hitting NOT NULL constraint
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM nutrition_cache WHERE ingredient_name = ? OR usda_food_id = ?",
            (ingredient_name, usda_food_id),
        )
        conn.execute(
            """
            INSERT INTO nutrition_cache (
                usda_food_id, ingredient_name, en_name,
                kcal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g,
                sodium_per_100g, fiber_per_100g, vitc_per_100g, iron_per_100g,
                calcium_per_100g, potassium_per_100g,
                vitd_per_100g, vita_per_100g, magnesium_per_100g, zinc_per_100g,
                satfat_per_100g, monofat_per_100g, polyfat_per_100g,
                source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                usda_food_id, ingredient_name, en_name,
                nutrients.get("kcal"),    nutrients.get("protein"),
                nutrients.get("fat"),     nutrients.get("carbs"),
                nutrients.get("sodium"),  nutrients.get("fiber"),
                nutrients.get("vitc"),    nutrients.get("iron"),
                nutrients.get("calcium"), nutrients.get("potassium"),
                nutrients.get("vitd"),    nutrients.get("vita"),
                nutrients.get("magnesium"), nutrients.get("zinc"),
                nutrients.get("satfat"), nutrients.get("monofat"),
                nutrients.get("polyfat"),
                source,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def invalidate_cache(ingredient_name: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM nutrition_cache WHERE ingredient_name = ?",
            (ingredient_name,),
        )
        conn.commit()
    finally:
        conn.close()


def update_cached_nutrients(ingredient_name: str, nutrients: dict) -> None:
    """Overwrite nutrient columns for an existing cache row (user correction)."""
    _COL_MAP = {
        "kcal": "kcal_per_100g", "protein": "protein_per_100g",
        "fat": "fat_per_100g", "carbs": "carbs_per_100g",
        "sodium": "sodium_per_100g", "fiber": "fiber_per_100g",
        "vitc": "vitc_per_100g", "iron": "iron_per_100g",
        "calcium": "calcium_per_100g", "potassium": "potassium_per_100g",
        "vitd": "vitd_per_100g", "vita": "vita_per_100g",
        "magnesium": "magnesium_per_100g", "zinc": "zinc_per_100g",
        "satfat": "satfat_per_100g", "monofat": "monofat_per_100g",
        "polyfat": "polyfat_per_100g",
        "en_name": "en_name",
    }
    sets = ", ".join(f"{_COL_MAP[k]}=?" for k in nutrients if k in _COL_MAP)
    vals = [nutrients[k] for k in nutrients if k in _COL_MAP]
    if not sets:
        return
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE nutrition_cache SET {sets}, source='manual' WHERE ingredient_name=?",
            (*vals, ingredient_name),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_cached_names() -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ingredient_name FROM nutrition_cache ORDER BY ingredient_name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()
