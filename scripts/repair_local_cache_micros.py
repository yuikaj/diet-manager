"""一次性修复：把 local_nutrition.json 里有、但缓存里丢失的营养素补回 nutrition_cache。

背景：`utils/nutrition_lookup.lookup_ingredient()` 的 tier-2 分支曾经逐字段手写
构造对象，漏掉了 vitd / vita / magnesium / zinc 四项，并且把这份残缺数据写进了
tier-1 缓存。因为 tier-1 会 shadow tier-2，这个丢失是永久的——之后每次查询都命中
缓存，再也不会回到 JSON 重读。「🔄 同步 local_nutrition.json」按钮也救不回来，
它跳过所有已在缓存中的名字。

代码本身已修（现在两边共用 _NUTRIENT_KEYS），这个脚本处理存量的坏行。

只填 NULL，不覆盖已有值：部分条目后来被 ai_fill_micronutrients.py 补过更好的数据，
不能被 JSON 里的旧值盖掉。

用法：
    python3.9 scripts/repair_local_cache_micros.py --dry-run
    python3.9 scripts/repair_local_cache_micros.py
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR, DB_PATH

# 当年被漏掉的四项是主角，脂肪细分一并检查（它进 cache 的路径同样经过 tier 2）
_FIELDS = ("vitd", "vita", "magnesium", "zinc", "satfat", "monofat", "polyfat")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    args = ap.parse_args()

    with open(DATA_DIR / "local_nutrition.json", encoding="utf-8") as f:
        local = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM nutrition_cache").fetchall()

    repairs: list = []
    for row in rows:
        entry = local.get(row["ingredient_name"])
        if not entry:
            continue
        per100 = entry.get("per_100g") or {}
        patch = {
            f: per100[f] for f in _FIELDS
            if per100.get(f) is not None and row[f"{f}_per_100g"] is None
        }
        if patch:
            repairs.append((row["ingredient_name"], patch))

    if not repairs:
        print("✅ 没有需要修复的行")
        conn.close()
        return

    n_fields = sum(len(p) for _, p in repairs)
    print(f"发现 {len(repairs)} 条食材、共 {n_fields} 个字段可从 local_nutrition.json 补回：\n")
    for name, patch in repairs[:20]:
        detail = "  ".join(f"{k}={v}" for k, v in sorted(patch.items()))
        print(f"  {name:12s}  {detail}")
    if len(repairs) > 20:
        print(f"  …… 另有 {len(repairs) - 20} 条")

    if args.dry_run:
        print("\n(--dry-run，未写入)")
        conn.close()
        return

    for name, patch in repairs:
        sets = ", ".join(f"{f}_per_100g=?" for f in patch)
        conn.execute(
            f"UPDATE nutrition_cache SET {sets} WHERE ingredient_name=?",
            (*patch.values(), name),
        )
    conn.commit()
    conn.close()
    print(f"\n✅ 已修复 {len(repairs)} 条食材的 {n_fields} 个字段")


if __name__ == "__main__":
    main()
