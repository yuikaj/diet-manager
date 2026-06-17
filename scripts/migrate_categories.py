"""
Migrate recipe categories from old 4-way system to new 3-way system.

Old: 纯蛋白 | 荤菜 | 半荤半素 | 纯素
New: 纯蛋白 | 半蛋白半素 | 纯素

Classification rules (from non-condiment ingredient analysis):
  has animal protein + has veg  → 半蛋白半素
  has animal protein + no veg   → 纯蛋白
  has plant protein (豆腐皮/腐竹) + no animal protein → 半蛋白半素
  no protein                     → 纯素

Form tags (汤, 凉拌, 主食) are preserved unchanged.

Usage:
    python3.9 scripts/migrate_categories.py
"""
import json
import sys

sys.path.insert(0, ".")
from db.init_db import get_connection

_OLD_MEAT   = {"纯蛋白", "荤菜", "半荤半素", "纯素"}
_FORM_TAGS  = {"汤", "凉拌", "主食"}
_PLANT_PROT = {"豆腐皮", "腐竹", "豆腐"}
# Keywords for unlisted ingredient names (e.g., "牛肉 (Flap meat)")
_PROT_KW    = {"牛", "猪", "鸡", "鸭", "鱼", "虾", "蟹", "羊", "排", "腩", "腿", "翅", "丸", "肉末", "肉丝"}


def _has_protein_keyword(name: str) -> bool:
    return any(kw in name for kw in _PROT_KW)


def migrate() -> None:
    conn = get_connection()

    # Build ingredient-name → category map from inventory.
    # Priority when same name appears in multiple categories:
    #   protein > leafy_veg/staple_veg > dry_goods > seasoning
    _PRIO = {"protein": 4, "leafy_veg": 3, "staple_veg": 3,
             "dry_goods": 2, "seasoning": 1}
    inv_cat: dict = {}
    for row in conn.execute("SELECT name, category FROM inventory").fetchall():
        name, cat = row["name"], row["category"]
        if _PRIO.get(cat, 0) > _PRIO.get(inv_cat.get(name, ""), 0):
            inv_cat[name] = cat

    recipes = conn.execute("SELECT id, name, category FROM recipes").fetchall()
    updated = 0

    for rec in recipes:
        rid  = rec["id"]
        old_cats: list = json.loads(rec["category"] or "[]")

        # Preserve form tags
        form_tags = [c for c in old_cats if c in _FORM_TAGS]

        # Analyse non-condiment ingredients
        ings = conn.execute(
            "SELECT name FROM ingredients WHERE recipe_id=? AND is_condiment=0", (rid,)
        ).fetchall()

        has_animal  = False
        has_veg     = False
        has_plant   = False

        for ing in ings:
            n = ing["name"]
            cat = inv_cat.get(n)
            if cat == "protein":
                has_animal = True
            elif cat in ("leafy_veg", "staple_veg"):
                has_veg = True
            elif cat == "dry_goods" and n in _PLANT_PROT:
                has_plant = True
            elif cat is None and _has_protein_keyword(n):
                # Unlisted ingredient guessed as protein
                has_animal = True

        # Derive new 荤素 tag
        if has_animal and has_veg:
            meat_tag = "半蛋白半素"
        elif has_animal:
            meat_tag = "纯蛋白"
        elif has_plant:
            meat_tag = "半蛋白半素"
        else:
            meat_tag = "纯素"

        new_cats = [meat_tag] + form_tags
        new_json = json.dumps(new_cats, ensure_ascii=False)

        if new_json != rec["category"]:
            conn.execute("UPDATE recipes SET category=? WHERE id=?", (new_json, rid))
            print(f"  {rec['name']}: {old_cats} → {new_cats}")
            updated += 1

    conn.commit()
    conn.close()
    print(f"\n迁移完成：{updated} 条菜谱已更新，{len(recipes) - updated} 条无需变更。")


if __name__ == "__main__":
    migrate()
