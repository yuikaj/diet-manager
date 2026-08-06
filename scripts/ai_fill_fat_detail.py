"""用 Gemini 补脂肪细分（饱和/单不饱和/多不饱和），按「实际贡献的脂肪量」排序。

为什么不按字母序或全量跑：菜谱库里 454 种含脂肪食材，但脂肪高度集中——
前 46 种就占了 80% 的总脂肪摄入（鸡腿/五花肉/黄油/各种油）。按贡献排序意味着
跑几十条就能让「饱和脂肪」这个数字变得可用，而不是等 454 条全部跑完。

USDA 那条路（backfill_fat_detail.py）只能覆盖 37 条带真实 FDC ID 的记录，
其余 600+ 条来自 local_nutrition.json，没有 ID 可查，只能走这里。

查不到就写 null 而不是 0：界面靠「是否为 NULL」判断该食材有没有细分数据，
写 0 会让它被当成「确实不含饱和脂肪」，从而虚报覆盖率。

用法：
    python3.9 scripts/ai_fill_fat_detail.py --top 46      # 覆盖 80% 脂肪摄入
    python3.9 scripts/ai_fill_fat_detail.py --top 10 --dry-run
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.init_db import get_connection
from db.nutrition import update_cached_nutrients

# gemini-2.0-flash is no longer available on the free tier (API returns
# "limit: 0"), so use the same model the in-app AI features already run on.
_MODEL = "gemini-flash-lite-latest"
_BATCH = 12


def _ranked_targets(top: int) -> list:
    """Ingredients without a breakdown, ordered by how much fat they contribute."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT i.name, SUM(i.amount)/100.0 * n.fat_per_100g AS fat_g
            FROM ingredients i JOIN nutrition_cache n ON n.ingredient_name = i.name
            WHERE i.unit = 'g'
              AND COALESCE(n.fat_per_100g, 0) > 0
              AND n.satfat_per_100g IS NULL
            GROUP BY i.name
            HAVING fat_g > 0
            ORDER BY fat_g DESC
        """).fetchall()
    finally:
        conn.close()
    return [(r["name"], r["fat_g"]) for r in rows[:top]]


_PROMPT = """你是营养数据专家。给出下列食材每 100g 的脂肪细分。

食材：{names}

只返回 JSON 数组，每项：
{{"name": "食材名（原样返回）", "satfat": 数值, "monofat": 数值, "polyfat": 数值}}

要求：
- satfat=饱和脂肪, monofat=单不饱和, polyfat=多不饱和，单位均为 g/100g
- 三者之和应略小于或接近该食材的总脂肪
- 生鲜食材按生重计；确实不确定的填 null，不要猜 0
- 不要任何解释文字或 markdown 包裹"""


def _ask(client, names: list) -> list:
    from google.genai import types
    resp = client.models.generate_content(
        model=_MODEL,
        contents=_PROMPT.format(names="、".join(names)),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(resp.text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=46)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = _ranked_targets(args.top)
    if not targets:
        print("没有需要补的食材")
        return

    print(f"待补 {len(targets)} 种（按脂肪贡献排序）")
    if args.dry_run:
        for n, g in targets:
            print(f"  {n:<16} 贡献 {g:>7.0f} g 脂肪")
        return

    from google import genai
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    names = [n for n, _ in targets]
    filled = skipped = 0
    for i in range(0, len(names), _BATCH):
        chunk = names[i:i + _BATCH]
        try:
            for item in _ask(client, chunk):
                nm = item.get("name")
                vals = {k: item.get(k) for k in ("satfat", "monofat", "polyfat")
                        if item.get(k) is not None}
                if nm and vals:
                    update_cached_nutrients(nm, vals)
                    filled += 1
                else:
                    skipped += 1
        except Exception as e:
            print(f"  批次 {i//_BATCH + 1} 失败：{e}")
            skipped += len(chunk)
        print(f"  已处理 {min(i+_BATCH, len(names))}/{len(names)}（补齐 {filled}）")
        time.sleep(4)

    print(f"\n完成：补齐 {filled} 种 · 跳过 {skipped} 种")


if __name__ == "__main__":
    main()
