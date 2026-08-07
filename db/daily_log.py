"""CRUD for daily_logs.

The old `_BFST_DEFAULTS` / `_LUNCH_DEFAULTS` estimate dicts and the
get/update_default_preset() helpers that read them are gone. Those were the
PRD-era hardcoded breakfast/lunch numbers whose micronutrients were badly wrong
(维A 60µg against a real ~630µg, 镁 80mg against ~275mg) — the main reason those
two nutrients looked permanently deficient. 早午餐 now goes through the nutrition
engine from the real ingredient lists in views/nutrition.py (_BFST_INGS /
_LUNCH_INGS), so it tracks the ingredient database as that improves. Nothing
called these any more; leaving them here was an invitation to wire the wrong
numbers back in. The meal_presets table itself is untouched.
"""
import json
import uuid
from typing import Optional
from db.init_db import get_connection

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
