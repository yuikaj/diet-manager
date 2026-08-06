"""为已缓存的食材回填脂肪细分（饱和 / 单不饱和 / 多不饱和）。

migration step 15 加了三个字段，但存量的 660+ 条缓存是空的。这些条目绝大多数
带 usda_food_id，可以直接按 ID 回查 USDA 拿到细分数据，不用重新做名称匹配
（重新搜索有匹配错食材的风险——之前"金鲳鱼→金色葡萄干"那类问题就是这么来的）。

查不到的保持 NULL 而不是写 0：「还没数据」和「确实不含饱和脂肪」必须能区分，
否则界面上会对着几百条没数据的食材信誓旦旦地显示 0。

用法：
    python3.9 scripts/backfill_fat_detail.py            # 全量（跳过已有数据的）
    python3.9 scripts/backfill_fat_detail.py --limit 20 # 先小批量试
    python3.9 scripts/backfill_fat_detail.py --dry-run
"""
import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import USDA_API_KEY
from db.init_db import get_connection

_FAT_IDS = {1258: "satfat", 1292: "monofat", 1293: "polyfat"}
_API = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
_SLEEP = 0.4   # USDA free tier allows ~1000 req/hour; this keeps well under it


def _targets(limit=None) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ingredient_name, usda_food_id FROM nutrition_cache "
            "WHERE usda_food_id IS NOT NULL "
            "  AND usda_food_id GLOB '[0-9]*' "          # real FDC ids only
            "  AND satfat_per_100g IS NULL "             # skip already-filled
            "  AND COALESCE(fat_per_100g, 0) > 0 "       # fat-free foods: nothing to split
            "ORDER BY ingredient_name"
        ).fetchall()
    finally:
        conn.close()
    return rows[:limit] if limit else rows


def _fetch(fdc_id: str) -> dict:
    r = requests.get(_API.format(fdc_id=fdc_id),
                     params={"api_key": USDA_API_KEY}, timeout=20)
    if r.status_code != 200:
        return {}
    out = {}
    for n in r.json().get("foodNutrients", []):
        nid = (n.get("nutrient") or {}).get("id") or n.get("nutrientId")
        val = n.get("amount", n.get("value"))
        if nid in _FAT_IDS and val is not None:
            out[_FAT_IDS[nid]] = float(val)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = _targets(args.limit)
    print(f"待回填 {len(rows)} 条")
    if args.dry_run:
        for r in rows[:20]:
            print(f"  {r['ingredient_name']}  (fdc {r['usda_food_id']})")
        return

    filled = no_data = failed = 0
    conn = get_connection()
    try:
        for i, r in enumerate(rows, 1):
            name, fdc = r["ingredient_name"], str(r["usda_food_id"])
            try:
                vals = _fetch(fdc)
            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(rows)}] {name}: 请求失败 {e}")
                time.sleep(_SLEEP)
                continue

            if not vals:
                no_data += 1
            else:
                conn.execute(
                    "UPDATE nutrition_cache SET satfat_per_100g=?, "
                    "monofat_per_100g=?, polyfat_per_100g=? WHERE ingredient_name=?",
                    (vals.get("satfat"), vals.get("monofat"), vals.get("polyfat"), name),
                )
                conn.commit()
                filled += 1
            if i % 25 == 0:
                print(f"  [{i}/{len(rows)}] 已补 {filled} · USDA无数据 {no_data} · 失败 {failed}")
            time.sleep(_SLEEP)
    finally:
        conn.close()

    print(f"\n完成：补齐 {filled} 条 · USDA 未提供 {no_data} 条 · 失败 {failed} 条")


if __name__ == "__main__":
    main()
