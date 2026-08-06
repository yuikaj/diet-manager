"""用 Gemini 补微量营养素缺口（维A / 镁 / 锌 / 维D / 钾 等），按实际用量排序。

为什么需要：nutrition_cache 里维A 只有 28% 的条目有数据、维D 只有 11%，其余是
NULL。查询时 NULL 按 0 计入，于是「7日 DRI 热力图」上维A 常年 🔴 11-32%——
那基本是数据缺口，不是真的吃不够。番茄、鸡蛋、土豆、洋葱这些维A 大户全都空着。

按「菜谱累计用量」排序而不是字母序：用量高度集中，补几十种就能把加权覆盖率
从 44% 拉到 80%+，不必等 300 多条全部跑完。

查不到就跳过（保持 NULL），不写 0——写 0 会让「没数据」伪装成「确实不含」，
既污染数据又虚报覆盖率。

用法：
    python3.9 scripts/ai_fill_micronutrients.py --nutrient vita --top 40
    python3.9 scripts/ai_fill_micronutrients.py --nutrient vita,magnesium,zinc --top 40
    python3.9 scripts/ai_fill_micronutrients.py --nutrient vita --top 10 --dry-run
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

_MODEL = "gemini-flash-lite-latest"
_BATCH = 12

# key → (DB column, 单位, 中文名)
_NUTRIENTS = {
    "vita":      ("vita_per_100g",      "µg RAE", "维生素A"),
    "vitd":      ("vitd_per_100g",      "µg",     "维生素D"),
    "vitc":      ("vitc_per_100g",      "mg",     "维生素C"),
    "magnesium": ("magnesium_per_100g", "mg",     "镁"),
    "zinc":      ("zinc_per_100g",      "mg",     "锌"),
    "potassium": ("potassium_per_100g", "mg",     "钾"),
    "calcium":   ("calcium_per_100g",   "mg",     "钙"),
    "iron":      ("iron_per_100g",      "mg",     "铁"),
}


def _ranked_targets(col: str, top: int) -> list:
    """Ingredients missing this nutrient, ordered by total grams used in recipes."""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT i.name, SUM(i.amount) AS grams
            FROM ingredients i JOIN nutrition_cache n ON n.ingredient_name = i.name
            WHERE i.unit = 'g' AND n.{col} IS NULL
            GROUP BY i.name
            ORDER BY grams DESC
        """).fetchall()
    finally:
        conn.close()
    return [(r["name"], r["grams"]) for r in rows[:top]]


_PROMPT = """你是营养数据专家。给出下列食材每 100g 的{cn}含量（单位 {unit}）。

食材：{names}

只返回 JSON 数组，每项：{{"name": "食材名（原样返回）", "{key}": 数值}}

要求：
- 生鲜食材按生重计
- 确实查不到或该食材本身不含此营养素时填 null，不要猜 0
- 不要任何解释文字或 markdown 包裹"""


def _ask(client, key: str, names: list) -> list:
    from google.genai import types
    _, unit, cn = _NUTRIENTS[key]
    resp = client.models.generate_content(
        model=_MODEL,
        contents=_PROMPT.format(cn=cn, unit=unit, key=key, names="、".join(names)),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(resp.text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nutrient", default="vita",
                    help="逗号分隔，可选：" + "/".join(_NUTRIENTS))
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keys = [k.strip() for k in args.nutrient.split(",") if k.strip() in _NUTRIENTS]
    if not keys:
        print("没有有效的营养素名")
        return

    client = None
    for key in keys:
        col, unit, cn = _NUTRIENTS[key]
        targets = _ranked_targets(col, args.top)
        print(f"\n=== {cn} ({key}) ：待补 {len(targets)} 种 ===")
        if args.dry_run:
            for n, g in targets[:15]:
                print(f"  {n:<16} 累计用量 {g:>7.0f} g")
            continue
        if not targets:
            continue

        if client is None:
            from google import genai
            client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        names = [n for n, _ in targets]
        filled = skipped = 0
        for i in range(0, len(names), _BATCH):
            chunk = names[i:i + _BATCH]
            try:
                for item in _ask(client, key, chunk):
                    nm, val = item.get("name"), item.get(key)
                    if nm and val is not None:
                        update_cached_nutrients(nm, {key: float(val)})
                        filled += 1
                    else:
                        skipped += 1
            except Exception as e:
                print(f"  批次失败：{e}")
                skipped += len(chunk)
            print(f"  已处理 {min(i+_BATCH, len(names))}/{len(names)}（补齐 {filled}）")
            time.sleep(4)
        print(f"  {cn} 完成：补齐 {filled} · 跳过 {skipped}")


if __name__ == "__main__":
    main()
