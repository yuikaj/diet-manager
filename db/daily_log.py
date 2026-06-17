"""CRUD for daily_logs and meal_presets tables."""
import json
import uuid
from typing import Optional
from db.init_db import get_connection

# Default nutrition values (per person) — mirrors nutrition.py hardcoded constants
_BFST_DEFAULTS = dict(
    kcal=580, protein=30, fat=18, carbs=72,
    sodium=280, fiber=16, vitc=8, iron=4,
    calcium=350, potassium=600, vitd=1.5, vita=60,
    magnesium=80, zinc=3,
)
_LUNCH_DEFAULTS = dict(
    kcal=210, protein=15, fat=8, carbs=18,
    sodium=150, fiber=1, vitc=0, iron=0.5,
    calcium=400, potassium=500, vitd=2.0, vita=0,
    magnesium=30, zinc=1,
)


# ── Meal presets ──────────────────────────────────────────────

def get_default_preset(meal_type: str) -> dict:
    """Return nutrition facts dict for the default preset of a meal type.
    Falls back to hardcoded estimates if no DB preset exists.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT items FROM meal_presets WHERE meal_type=? AND is_default=1 LIMIT 1",
            (meal_type,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return _BFST_DEFAULTS if meal_type == "breakfast" else _LUNCH_DEFAULTS

    data = json.loads(row["items"])
    # Build a nutrition dict from DB preset (uses estimated_ fields or defaults)
    defaults = _BFST_DEFAULTS if meal_type == "breakfast" else _LUNCH_DEFAULTS
    return {
        "kcal":      float(data.get("estimated_kcal",    defaults["kcal"])),
        "protein":   float(data.get("estimated_protein_g", defaults["protein"])),
        "fat":       float(data.get("estimated_fat_g",   defaults["fat"])),
        "carbs":     float(data.get("estimated_carbs_g", defaults["carbs"])),
        "sodium":    float(data.get("estimated_sodium_mg", defaults["sodium"])),
        "fiber":     float(data.get("estimated_fiber_g", defaults["fiber"])),
        "vitc":      float(data.get("estimated_vitc_mg", defaults["vitc"])),
        "iron":      float(data.get("estimated_iron_mg", defaults["iron"])),
        "calcium":   float(data.get("estimated_calcium_mg", defaults["calcium"])),
        "potassium": float(data.get("estimated_potassium_mg", defaults["potassium"])),
        "vitd":      float(data.get("estimated_vitd_ug", defaults["vitd"])),
        "vita":      float(data.get("estimated_vita_ug", defaults["vita"])),
        "magnesium": float(data.get("estimated_magnesium_mg", defaults["magnesium"])),
        "zinc":      float(data.get("estimated_zinc_mg", defaults["zinc"])),
        "items":     data.get("items", []),
    }


def update_default_preset(meal_type: str, nutrition: dict) -> None:
    """Update the estimated nutrition values for the default preset of a meal type."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, items FROM meal_presets WHERE meal_type=? AND is_default=1 LIMIT 1",
            (meal_type,),
        ).fetchone()

        mapping = {
            "kcal":      "estimated_kcal",
            "protein":   "estimated_protein_g",
            "fat":       "estimated_fat_g",
            "carbs":     "estimated_carbs_g",
            "sodium":    "estimated_sodium_mg",
            "fiber":     "estimated_fiber_g",
            "vitc":      "estimated_vitc_mg",
            "iron":      "estimated_iron_mg",
            "calcium":   "estimated_calcium_mg",
            "potassium": "estimated_potassium_mg",
            "vitd":      "estimated_vitd_ug",
            "vita":      "estimated_vita_ug",
            "magnesium": "estimated_magnesium_mg",
            "zinc":      "estimated_zinc_mg",
        }

        if row:
            data = json.loads(row["items"])
            for nutr_key, db_key in mapping.items():
                if nutr_key in nutrition:
                    data[db_key] = float(nutrition[nutr_key])
            conn.execute(
                "UPDATE meal_presets SET items=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), row["id"]),
            )
        else:
            data = {db_key: float(nutrition.get(nutr_key, 0))
                    for nutr_key, db_key in mapping.items()}
            conn.execute(
                "INSERT INTO meal_presets (id, name, meal_type, items, is_default) VALUES (?,?,?,?,1)",
                (str(uuid.uuid4()),
                 "默认早餐" if meal_type == "breakfast" else "默认午餐",
                 meal_type,
                 json.dumps(data, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()


# ── Daily logs ────────────────────────────────────────────────

def save_daily_log(
    date: str,
    total: dict,
    dinner_rids: list,
    fruit_names: list,
    fruit_g: int,
    *,
    bfst_skip: bool = False,
    bfst_custom_ings: list = None,
    lunch_skip: bool = False,
    lunch_custom_ings: list = None,
    staple_ings: list = None,
    ingredients_snapshot: list = None,
) -> None:
    """Upsert today's nutrition log."""
    conn = get_connection()
    try:
        bfst_mode  = "skip" if bfst_skip else ("custom" if bfst_custom_ings else "default")
        lunch_mode = "skip" if lunch_skip else ("custom" if lunch_custom_ings else "default")
        breakfast_json = json.dumps({"mode": bfst_mode,  "custom": bfst_custom_ings  or []}, ensure_ascii=False)
        lunch_json     = json.dumps({"mode": lunch_mode, "custom": lunch_custom_ings or []}, ensure_ascii=False)
        extra_json     = json.dumps({"fruits": fruit_names, "fruit_g_each": fruit_g}, ensure_ascii=False)

        existing = conn.execute(
            "SELECT id FROM daily_logs WHERE date=?", (date,)
        ).fetchone()

        fields = dict(
            breakfast=breakfast_json,
            lunch=lunch_json,
            dinner_recipe_ids=json.dumps(dinner_rids, ensure_ascii=False),
            dinner_placeholder=extra_json,
            dinner_staple=json.dumps(staple_ings or [], ensure_ascii=False),
            ingredients_snapshot=json.dumps(ingredients_snapshot or [], ensure_ascii=False),
            total_nutrients_json=json.dumps(total, ensure_ascii=False),
            total_kcal=float(total.get("kcal", 0)),
            total_protein=float(total.get("protein", 0)),
            total_fat=float(total.get("fat", 0)),
            total_carbs=float(total.get("carbs", 0)),
            total_sodium=float(total.get("sodium", 0)),
            total_fiber=float(total.get("fiber", 0)),
        )

        if existing:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE daily_logs SET {sets} WHERE date=?",
                list(fields.values()) + [date],
            )
        else:
            fields["id"]   = str(uuid.uuid4())
            fields["date"] = date
            cols = ", ".join(fields.keys())
            vals = ", ".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO daily_logs ({cols}) VALUES ({vals})",
                list(fields.values()),
            )
        conn.commit()
    finally:
        conn.close()


def get_daily_log(date: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM daily_logs WHERE date=?", (date,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_recent_logs(n: int = 14) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date, total_kcal, total_protein, total_fat, total_carbs, total_sodium, total_fiber "
            "FROM daily_logs ORDER BY date DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_logs_full(n: int = 14) -> list:
    """Return recent logs including snapshot and full nutrient JSON for 7-day analysis."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date, total_kcal, total_protein, total_fat, total_carbs, "
            "total_sodium, total_fiber, total_nutrients_json, ingredients_snapshot "
            "FROM daily_logs ORDER BY date DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
