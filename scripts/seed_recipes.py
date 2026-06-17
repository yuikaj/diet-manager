"""
Seed 30 mock Chinese recipes into the DB.
Also sets demo inventory quantities (only if currently 0, so user data is not overwritten).

Usage:
    python3.9 scripts/seed_recipes.py
"""
import json
import sys
import uuid

sys.path.insert(0, ".")
from db.init_db import get_connection

# ── Recipe data ───────────────────────────────────────────────────────────────
# Each entry: (name, category[], cooking_method[], uses_wok, cook_time_min,
#              is_parallel, prep_difficulty, ingredients[])
# Ingredient tuple: (name, amount_g, is_condiment, intake_ratio)
# Names MUST match inventory item names for recommender inventory-check to work.

RECIPES = [
    # ── 纯素 · 炒锅 (quick-wok ≤5min) ─────────────────────────────────────
    ("蒜蓉西兰花",
     ["纯素"], ["炒"], 1, 5, 0, "简单",
     [("西兰花", 300, 0, 1.0), ("冻蒜", 10, 1, 0.25)]),

    ("清炒油菜",
     ["纯素"], ["炒"], 1, 3, 0, "简单",
     [("油菜", 300, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    ("蒜蓉芥兰",
     ["纯素"], ["炒"], 1, 5, 0, "简单",
     [("芥兰", 300, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    ("清炒茼蒿",
     ["纯素"], ["炒"], 1, 3, 0, "简单",
     [("茼蒿", 250, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    ("清炒豆苗",
     ["纯素"], ["炒"], 1, 3, 0, "简单",
     [("豆苗", 250, 0, 1.0), ("冻蒜", 6, 1, 0.25)]),

    ("清炒莴笋",
     ["纯素"], ["炒"], 1, 5, 0, "简单",
     [("莴笋", 300, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    # ── 纯素 · 炒锅 (standard wok >5min) ──────────────────────────────────
    ("干煸四季豆",
     ["纯素"], ["炒"], 1, 10, 0, "中等",
     [("四季豆", 350, 0, 1.0), ("冻蒜", 10, 1, 0.25)]),

    ("韭菜炒豆腐皮",
     ["纯素"], ["炒"], 1, 6, 0, "简单",
     [("韭菜", 200, 0, 1.0), ("豆腐皮", 100, 0, 1.0), ("冻蒜", 6, 1, 0.25)]),

    ("西芹炒百合",
     ["纯素"], ["炒"], 1, 8, 0, "简单",
     [("西芹", 250, 0, 1.0), ("冻蒜", 6, 1, 0.25)]),

    ("虎皮青椒",
     ["纯素"], ["炒"], 1, 10, 0, "简单",
     [("青彩椒", 250, 0, 1.0), ("冻蒜", 6, 1, 0.25)]),

    # ── 纯素 · 蒸/凉拌 (parallel, no wok) ────────────────────────────────
    ("凉拌黄瓜",
     ["纯素", "凉拌"], ["凉拌"], 0, 10, 1, "简单",
     [("黄瓜", 300, 0, 1.0), ("冻蒜", 8, 1, 0.25), ("香菜", 10, 1, 0.5)]),

    ("蒸茄子",
     ["纯素"], ["蒸"], 0, 15, 1, "简单",
     [("茄子", 350, 0, 1.0), ("冻蒜", 10, 1, 0.25)]),

    ("凉拌木耳",
     ["纯素", "凉拌"], ["凉拌"], 0, 20, 1, "简单",
     [("木耳", 50, 0, 1.0), ("冻蒜", 8, 1, 0.25), ("香菜", 10, 1, 0.5)]),

    # ── 纯素 · 汤 ─────────────────────────────────────────────────────────
    ("鸡毛菜汤",
     ["纯素", "汤"], ["汤"], 1, 8, 0, "简单",
     [("鸡毛菜", 200, 0, 1.0), ("虾皮", 10, 1, 0.5)]),

    ("西洋菜汤",
     ["纯素", "汤"], ["汤"], 1, 15, 0, "简单",
     [("西洋菜", 300, 0, 1.0), ("冻姜", 5, 1, 0.25)]),

    ("金针菇豆腐汤",
     ["纯素", "汤"], ["汤"], 1, 12, 0, "简单",
     [("金针菇", 150, 0, 1.0), ("冻姜", 5, 1, 0.25)]),

    # ── 纯蛋白 · 蒸/煮 (parallel) ────────────────────────────────────────
    ("清蒸鲈鱼",
     ["纯蛋白"], ["蒸"], 0, 15, 1, "中等",
     [("海鲈鱼", 500, 0, 1.0), ("冻姜", 8, 1, 0.25), ("葱", 15, 1, 0.5)]),

    ("清蒸金鲳鱼",
     ["纯蛋白"], ["蒸"], 0, 20, 1, "中等",
     [("金鲳鱼", 500, 0, 1.0), ("冻姜", 8, 1, 0.25), ("葱", 15, 1, 0.5)]),

    ("盐水白虾",
     ["纯蛋白"], ["煮"], 0, 8, 1, "简单",
     [("白虾", 400, 0, 1.0), ("冻姜", 5, 1, 0.25)]),

    ("白灼北极虾",
     ["纯蛋白"], ["煮"], 0, 8, 1, "简单",
     [("北极虾", 400, 0, 1.0), ("冻姜", 5, 1, 0.25)]),

    ("烤鸡腿",
     ["纯蛋白"], ["烤"], 0, 40, 1, "简单",
     [("鸡腿", 500, 0, 1.0), ("冻蒜", 10, 1, 0.25), ("冻姜", 5, 1, 0.25)]),

    # ── 纯蛋白 · 煎 (wok, standard) ──────────────────────────────────────
    ("煎三文鱼",
     ["纯蛋白"], ["煎"], 1, 10, 0, "简单",
     [("三文鱼", 300, 0, 1.0), ("冻姜", 5, 1, 0.25)]),

    ("香煎带鱼",
     ["纯蛋白"], ["煎"], 1, 20, 0, "中等",
     [("带鱼", 400, 0, 1.0), ("冻姜", 8, 1, 0.25)]),

    # ── 荤菜 ──────────────────────────────────────────────────────────────
    ("红烧猪五花",
     ["荤菜"], ["焖"], 1, 45, 0, "中等",
     [("猪五花", 500, 0, 1.0), ("土豆", 200, 0, 1.0),
      ("冻姜", 10, 1, 0.25), ("冻蒜", 10, 1, 0.25)]),

    ("番茄炖牛腩",
     ["荤菜"], ["炖"], 0, 90, 1, "中等",
     [("牛腩", 500, 0, 1.0), ("番茄", 300, 0, 1.0),
      ("洋葱", 100, 0, 1.0), ("冻姜", 8, 1, 0.25)]),

    ("红烧鸡翅",
     ["荤菜"], ["焖"], 1, 30, 0, "简单",
     [("鸡翅", 500, 0, 1.0), ("冻姜", 8, 1, 0.25), ("冻蒜", 8, 1, 0.25)]),

    ("葱爆猪肉丝",
     ["荤菜"], ["炒"], 1, 10, 0, "简单",
     [("猪肉丝", 300, 0, 1.0), ("葱", 100, 0, 1.0), ("冻姜", 5, 1, 0.25)]),

    # ── 半荤半素 ──────────────────────────────────────────────────────────
    ("四季豆炒肉丝",
     ["半荤半素"], ["炒"], 1, 10, 0, "简单",
     [("四季豆", 300, 0, 1.0), ("猪肉丝", 150, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    ("木耳炒肉",
     ["半荤半素"], ["炒"], 1, 10, 0, "简单",
     [("木耳", 50, 0, 1.0), ("猪肉丝", 200, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    ("番茄炒牛肉",
     ["半荤半素"], ["炒"], 1, 15, 0, "中等",
     [("炒牛肉", 300, 0, 1.0), ("番茄", 200, 0, 1.0), ("冻蒜", 6, 1, 0.25)]),

    ("花菜炒肉末",
     ["半荤半素"], ["炒"], 1, 10, 0, "简单",
     [("花菜", 300, 0, 1.0), ("猪肉末", 150, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),

    # ── 荤 · 汤 / 半荤半素 · 汤 ──────────────────────────────────────────
    ("萝卜牛尾汤",
     ["荤菜", "汤"], ["炖"], 0, 90, 1, "简单",
     [("牛尾", 500, 0, 1.0), ("萝卜", 300, 0, 1.0), ("冻姜", 10, 1, 0.25)]),

    ("肥牛金针菇",
     ["半荤半素", "汤"], ["煮"], 0, 15, 1, "简单",
     [("肥牛卷", 300, 0, 1.0), ("金针菇", 200, 0, 1.0), ("冻蒜", 8, 1, 0.25)]),
]

# ── Demo inventory quantities (only set if currently 0) ───────────────────────
DEMO_INVENTORY = {
    # leafy_veg
    "西兰花": 600, "油菜": 450, "芥兰": 400, "茼蒿": 350, "四季豆": 500,
    "莴笋": 300, "豆苗": 500, "韭菜": 300, "黄瓜": 400, "茄子": 400,
    "西洋菜": 450, "鸡毛菜": 350, "金针菇": 300, "花菜": 400, "萝卜": 500,
    "西芹": 300, "青彩椒": 250, "葱": 200,
    # protein
    "海鲈鱼": 500, "三文鱼": 300, "猪五花": 500, "猪肉丝": 400,
    "猪肉末": 300, "牛腩": 600, "牛尾": 500, "鸡翅": 600, "鸡腿": 500,
    "炒牛肉": 400, "白虾": 400, "北极虾": 400, "肥牛卷": 300, "金鲳鱼": 500,
    "带鱼": 400,
}

# Boolean items to set in_stock=1 if not already
DEMO_BOOL_INSTOCK = ["木耳", "豆腐皮", "虾皮", "土豆", "番茄", "白菜",
                     "卷心菜", "洋葱", "胡萝卜", "冻蒜", "冻姜", "香菜"]


def seed(skip_existing: bool = True) -> None:
    conn = get_connection()
    inserted = 0
    skipped = 0

    for (name, cats, methods, uses_wok, cook_time, is_par, diff, ings) in RECIPES:
        if skip_existing:
            existing = conn.execute(
                "SELECT id FROM recipes WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                skipped += 1
                continue

        rid = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO recipes
              (id, name, cooking_method, uses_wok, cook_time_min, is_parallel,
               prep_difficulty, category, tags, data_quality)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rid,
                name,
                json.dumps(methods, ensure_ascii=False),
                uses_wok,
                cook_time,
                is_par,
                diff,
                json.dumps(cats, ensure_ascii=False),
                "[]",
                "mock",
            ),
        )
        for (iname, amt, is_cond, ratio) in ings:
            conn.execute(
                """
                INSERT INTO ingredients
                  (id, recipe_id, name, amount, unit, is_condiment, intake_ratio)
                VALUES (?,?,?,?,?,?,?)
                """,
                (str(uuid.uuid4()), rid, iname, amt, "g", is_cond, ratio),
            )
        inserted += 1

    conn.commit()
    print(f"Recipes: {inserted} inserted, {skipped} skipped.")

    # ── Demo inventory quantities ────────────────────────────────────────────
    inv_updated = 0
    for name, qty in DEMO_INVENTORY.items():
        row = conn.execute(
            "SELECT id, quantity FROM inventory WHERE name = ? AND item_type = 'quantity'",
            (name,),
        ).fetchone()
        if row and (row["quantity"] is None or float(row["quantity"]) == 0):
            conn.execute(
                "UPDATE inventory SET quantity = ?, unit = 'g', updated_at = datetime('now','localtime') WHERE id = ?",
                (qty, row["id"]),
            )
            inv_updated += 1

    # Boolean items
    for name in DEMO_BOOL_INSTOCK:
        row = conn.execute(
            "SELECT id, in_stock FROM inventory WHERE name = ? AND item_type = 'boolean'",
            (name,),
        ).fetchone()
        if row and not row["in_stock"]:
            conn.execute(
                "UPDATE inventory SET in_stock = 1, updated_at = datetime('now','localtime') WHERE id = ?",
                (row["id"],),
            )
            inv_updated += 1

    conn.commit()
    conn.close()
    print(f"Inventory: {inv_updated} items updated with demo quantities (0→demo value only).")


if __name__ == "__main__":
    seed()
    print("Done.")
