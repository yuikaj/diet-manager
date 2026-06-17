"""CRUD for recipes and ingredients tables."""
import json
import uuid
from typing import Optional
from db.init_db import get_connection


# ─── Helpers ──────────────────────────────────────────────────

def _parse(row) -> dict:
    d = dict(row)
    d["cooking_method"] = json.loads(d.get("cooking_method") or "[]")
    d["tags"]  = json.loads(d.get("tags")  or "[]")
    d["steps"] = json.loads(d.get("steps") or "[]")
    d["pairing_ids"] = json.loads(d.get("pairing_ids") or "[]")
    # category 兼容旧字符串格式
    raw_cat = d.get("category") or "[]"
    if raw_cat.startswith("["):
        d["category"] = json.loads(raw_cat)
    else:
        d["category"] = [raw_cat] if raw_cat else []
    return d


def _insert_ingredient(conn, recipe_id: str, ing: dict) -> None:
    conn.execute(
        """INSERT INTO ingredients (id, recipe_id, name, amount, unit, is_condiment, intake_ratio)
           VALUES (?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), recipe_id,
            ing["name"],
            float(ing.get("amount") or 0),
            ing.get("unit", "g"),
            1 if ing.get("is_condiment") else 0,
            float(ing.get("intake_ratio", 1.0)),
        ),
    )


# ─── Read ─────────────────────────────────────────────────────

def get_all_recipes(
    category: Optional[str] = None,
    data_quality: Optional[str] = None,
    cooking_method: Optional[str] = None,
    search: Optional[str] = None,
) -> list:
    sql = "SELECT * FROM recipes WHERE 1=1"
    params: list = []
    if category:
        # category 存为 JSON 数组，用 LIKE 匹配数组内包含该值
        sql += ' AND category LIKE ?'; params.append(f'%"{category}"%')
    if data_quality:
        sql += " AND data_quality=?";   params.append(data_quality)
    if cooking_method:
        # cooking_method is stored as JSON array, e.g. '["炒","蒸"]'
        sql += ' AND cooking_method LIKE ?'; params.append(f'%"{cooking_method}"%')
    if search:
        sql += " AND name LIKE ?";      params.append(f"%{search}%")
    sql += " ORDER BY created_at DESC"
    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_parse(r) for r in rows]


def get_recipe(recipe_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    conn.close()
    return _parse(row) if row else None


def get_ingredients(recipe_id: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ingredients WHERE recipe_id=? ORDER BY is_condiment, name",
        (recipe_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_ingredients_grouped() -> dict:
    """Bulk-fetch ALL ingredients in a single query and return
    {recipe_id: [ingredient_dict, ...]}.

    Use this when you need ingredients for many/all recipes in one pass
    (e.g., the recommender's pool construction) — eliminates the N+1 query
    pattern of calling get_ingredients() in a loop.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ingredients ORDER BY recipe_id, is_condiment, name"
    ).fetchall()
    conn.close()
    grouped: dict = {}
    for r in rows:
        d = dict(r)
        grouped.setdefault(d["recipe_id"], []).append(d)
    return grouped


# ─── Write ────────────────────────────────────────────────────

def create_recipe(data: dict, ingredients: list) -> str:
    recipe_id = str(uuid.uuid4())
    conn = get_connection()
    active = int(data.get("active_time_min") or 30)
    idle   = int(data.get("idle_time_min")   or 0)
    conn.execute(
        """INSERT INTO recipes
           (id, name, source_url, cooking_method, uses_wok, prep_difficulty,
            active_time_min, idle_time_min, cook_time_min,
            is_parallel, category, tags, data_quality, notes,
            condiment_ratio, serving_ratio, steps, en_name, en_desc, zh_desc,
            cuisine, pairing_ids)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            recipe_id, data["name"], data.get("source_url"),
            json.dumps(data.get("cooking_method", []), ensure_ascii=False),
            1 if data.get("uses_wok") else 0,
            data.get("prep_difficulty", "中等"),
            active, idle, active + idle,
            1 if data.get("is_parallel") else 0,
            json.dumps(data.get("category", []), ensure_ascii=False),
            json.dumps(data.get("tags", []), ensure_ascii=False),
            data.get("data_quality", "needs_review"),
            data.get("notes") or None,
            float(data.get("condiment_ratio", 1.0)),
            float(data.get("serving_ratio", 1.0)),
            json.dumps(data.get("steps", []), ensure_ascii=False),
            data.get("en_name") or None,
            data.get("en_desc") or None,
            data.get("zh_desc") or None,
            data.get("cuisine") or None,
            json.dumps(data.get("pairing_ids", []), ensure_ascii=False),
        ),
    )
    for ing in ingredients:
        _insert_ingredient(conn, recipe_id, ing)
    conn.commit()
    conn.close()
    return recipe_id


def update_recipe(recipe_id: str, data: dict, ingredients: list) -> None:
    conn = get_connection()
    active = int(data.get("active_time_min") or 30)
    idle   = int(data.get("idle_time_min")   or 0)
    conn.execute(
        """UPDATE recipes SET
           name=?, source_url=?, cooking_method=?, uses_wok=?, prep_difficulty=?,
           active_time_min=?, idle_time_min=?, cook_time_min=?,
           is_parallel=?, category=?, tags=?, data_quality=?, notes=?,
           condiment_ratio=?, serving_ratio=?, steps=?, en_name=?, en_desc=?, zh_desc=?,
           cuisine=?, pairing_ids=?
           WHERE id=?""",
        (
            data["name"], data.get("source_url"),
            json.dumps(data.get("cooking_method", []), ensure_ascii=False),
            1 if data.get("uses_wok") else 0,
            data.get("prep_difficulty", "中等"),
            active, idle, active + idle,
            1 if data.get("is_parallel") else 0,
            json.dumps(data.get("category", []), ensure_ascii=False),
            json.dumps(data.get("tags", []), ensure_ascii=False),
            data.get("data_quality", "needs_review"),
            data.get("notes") or None,
            float(data.get("condiment_ratio", 1.0)),
            float(data.get("serving_ratio", 1.0)),
            json.dumps(data.get("steps", []), ensure_ascii=False),
            data.get("en_name") or None,
            data.get("en_desc") or None,
            data.get("zh_desc") or None,
            data.get("cuisine") or None,
            json.dumps(data.get("pairing_ids", []), ensure_ascii=False),
            recipe_id,
        ),
    )
    conn.execute("DELETE FROM ingredients WHERE recipe_id=?", (recipe_id,))
    for ing in ingredients:
        _insert_ingredient(conn, recipe_id, ing)
    conn.commit()
    conn.close()


def mark_cooked(recipe_id: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE recipes SET last_cooked=datetime('now','localtime') WHERE id=?",
        (recipe_id,),
    )
    conn.commit()
    conn.close()


def delete_recipe(recipe_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
    conn.commit()
    conn.close()


def recipe_exists_by_url(source_url: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM recipes WHERE source_url=?", (source_url,)
    ).fetchone()
    conn.close()
    return row is not None
