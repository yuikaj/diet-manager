"""
Scan complete+mock recipes for non-condiment ingredients missing from inventory.
For each missing name, guess category and add with a default stock level.
Also prints naming-inconsistency suspects for manual review.

Usage:
    python scripts/seed_missing_inventory.py [--dry-run]
"""
import sys
import difflib

sys.path.insert(0, "/Users/jian227/Documents/Projects/diet-manager")

from db.inventory import get_all_inventory, add_item
from db.recipes import get_all_recipes, get_ingredients
from db.init_db import get_connection

# ── Category heuristics ───────────────────────────────────────────────────────

_PROTEIN_KEYWORDS = [
    "肉", "牛", "猪", "鸡", "鸭", "鹅", "羊", "鱼", "虾", "蟹", "贝", "排骨",
    "肥牛", "五花", "腿", "筋", "腩", "肘", "蛋", "章鱼", "鱿鱼", "扇贝",
    "鳗鱼", "带鱼", "鲈鱼", "鳝", "鲳鱼", "eel", "鸽", "肠", "腊",
]
_LEAFY_KEYWORDS = [
    "菜", "菇", "苗", "藕", "莲", "茼蒿", "秋葵", "韭", "豆芽", "蒜苗",
    "芥兰", "茭白", "竹笋", "笋", "花椰", "花菜", "西兰", "丝瓜", "冬瓜",
    "萝卜", "莴笋", "西芹", "山药", "芋", "荷兰豆", "豆苗", "绣球",
]
_DRY_KEYWORDS = [
    "粉丝", "腐竹", "木耳", "海带", "魔芋", "豆皮", "豆腐干", "香干",
    "虾皮", "海米", "干", "淀粉", "面粉", "粉", "noodle", "面条",
]
_SKIP_ITEMS = {
    "水", "清水", "葱姜水", "杂菜", "各种海鲜", "其他可选",
    "火锅食材", "蔬菜", "蔬菜丁",
}

def _guess_category(name: str) -> tuple:
    """Returns (category, item_type). Check dry_goods first to avoid false protein matches."""
    for kw in _DRY_KEYWORDS:
        if kw in name:
            return "dry_goods", "boolean"
    for kw in _PROTEIN_KEYWORDS:
        if kw in name:
            return "protein", "quantity"
    for kw in _LEAFY_KEYWORDS:
        if kw in name:
            return "leafy_veg", "quantity"
    return "staple_veg", "boolean"


# ── Name aliases: recipe ingredient name → existing inventory name ─────────────
# Add entries here to fix naming mismatches instead of creating duplicates.
NAME_ALIASES: dict = {
    # Naming inconsistencies between recipes and inventory
    "西兰花":   "西蓝花",
    "全鸡腿":   "鸡腿",
    "鸡腿肉":   "鸡腿",
    "肥牛":     "肥牛卷",
    "肉末":     "猪肉末",
    "肉糜":     "猪肉末",
    "猪肉片":   "猪梅肉",
    "五花肉片": "猪五花",
    "黑猪五花": "猪五花",
    "肉丝":     "猪肉丝",
    "肉馅":     "猪肉末",
    "香菇":     "干香菇",
    "虾":       "白虾",
    "排骨":     "猪小排",
    # Cut/form variants that map to the same inventory item
    "胡萝卜丁": "胡萝卜",
    "芥兰苗":   "芥兰",
    "魔芋丝":   "魔芋",
    "干腐竹":   "腐竹",
    "生菜叶":   "生菜",
    "豆皮":     "豆腐皮",
}

# Items to skip (non-food or too vague to add sensibly)
SKIP_ITEMS = _SKIP_ITEMS | {
    "AP面粉", "Half and Half", "bread flour or AP flour", "crashed nut",
    "egg", "milk", "nutella", "parmesan cheese可用黄油代替",
    "番茄碎",   # condiment
    "葱姜水",
    "细砂糖(焦糖用)", "细砂糖(面糊用)",   # baking sugar, not a trackable pantry item
    "鲜奶油(打发用)", "鲜奶油(焦糖酱用)", # baking cream
    "汤种面粉",  # baking-specific prep step, not a standalone ingredient
    "晶球",      # tapioca pearls – niche
}


def main(dry_run: bool = False) -> None:
    inv = get_all_inventory()
    all_inv_names: set[str] = set()
    inv_name_to_item: dict[str, dict] = {}
    for items in inv.values():
        for item in items:
            all_inv_names.add(item["name"])
            inv_name_to_item[item["name"]] = item

    all_recipes = get_all_recipes()
    missing: dict = {}
    for r in all_recipes:
        if r.get("data_quality") not in ("complete", "mock"):
            continue
        for ing in get_ingredients(r["id"]):
            if ing.get("is_condiment"):
                continue
            name = ing["name"]
            if name in all_inv_names:
                continue
            if name in NAME_ALIASES and NAME_ALIASES[name] in all_inv_names:
                continue
            if name in SKIP_ITEMS:
                continue
            missing.setdefault(name, []).append(r["name"])

    print(f"\n{'[DRY RUN] ' if dry_run else ''}库存中真正缺失的食材（complete/mock 菜谱）：{len(missing)} 个\n")

    # Print name-alias summary
    print("── 命名别名映射（不新增，已指向现有库存项）─────────────────")
    for recipe_name, inv_name in NAME_ALIASES.items():
        status = "✓ 在库存" if inv_name in all_inv_names else "✗ 目标也不在库存"
        print(f"  {recipe_name:<20} → {inv_name}  [{status}]")

    print(f"\n── 新增食材 ─────────────────────────────────────────────────")
    added = 0
    for name, recipes in sorted(missing.items()):
        cat, itype = _guess_category(name)

        # Fuzzy-match suspect (might be a typo of existing item)
        close = difflib.get_close_matches(name, all_inv_names, n=1, cutoff=0.7)
        suspect = f"  ⚠ 疑似重复: {close[0]}" if close else ""

        print(f"  + {name:<30} → {cat} ({itype}){suspect}")
        print(f"      出现于: {', '.join(set(recipes))}")

        if not dry_run:
            qty_default = 500.0
            try:
                item_id = add_item(name, cat, itype)
                # Set default stock
                conn = get_connection()
                if itype == "quantity":
                    conn.execute("UPDATE inventory SET quantity=? WHERE id=?", (qty_default, item_id))
                else:
                    conn.execute("UPDATE inventory SET in_stock=1 WHERE id=?", (item_id,))
                conn.commit()
                conn.close()
                added += 1
            except Exception as e:
                print(f"      ✗ 插入失败: {e}")

    print(f"\n{'[DRY RUN] 将添加' if dry_run else '已添加'} {len(missing)} 个食材" +
          (f"，{added} 个成功" if not dry_run else ""))

    # Suggest fixing aliases in recipe DB
    print("\n── 建议修复的菜谱食材命名（让名称与库存一致）────────────────")
    for r in all_recipes:
        if r.get("data_quality") not in ("complete", "mock"):
            continue
        for ing in get_ingredients(r["id"]):
            if ing.get("is_condiment"):
                continue
            name = ing["name"]
            if name in NAME_ALIASES:
                print(f"  菜谱「{r['name']}」食材 {name} → 建议改为 {NAME_ALIASES[name]}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run)
