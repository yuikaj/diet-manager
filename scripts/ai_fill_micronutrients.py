"""用 Gemini 补微量营养素缺口（维A / 镁 / 锌 / 维D / 钾 等），按实际用量排序。

为什么需要：nutrition_cache 里维A 只有 28% 的条目有数据、维D 只有 11%，其余是
NULL。查询时 NULL 按 0 计入，于是「7日 DRI 热力图」上维A 常年 🔴 11-32%——
那基本是数据缺口，不是真的吃不够。番茄、鸡蛋、土豆、洋葱这些维A 大户全都空着。

按「菜谱累计用量」排序而不是字母序：用量高度集中，补几十种就能把加权覆盖率
从 44% 拉到 80%+，不必等 300 多条全部跑完。

**默认只补主料（一级食材）**，跳过调料：调料占菜谱克重约 38%，但经
condiment_ratio 折扣后实际摄入占比小得多，把配额花在生抽老抽上不划算。
要连调料一起补加 `--all-ingredients`。

**0 和 null 是两件事**：
  - 「确实不含」→ 写 0。蔬菜水果谷物的维生素D 就是 0，这是真实数据。
  - 「查不到 / 不确定」→ 保持 null。
prompt 早期版本把两者都归为 null（"该食材本身不含此营养素时填 null"），
结果跑维D 时模型对满屏蔬菜一律返回 null，一条都补不进去——而维D 恰好是
缺口最大的那一项。UI 靠这个区分"确实没吃到"和"没有数据"。

用法：
    python3.9 scripts/ai_fill_micronutrients.py --nutrient vita --top 40
    python3.9 scripts/ai_fill_micronutrients.py --nutrient vitd,vita,magnesium,zinc --top 60
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


# Recipe-import artefacts that aren't ingredients (mirrors views/nutrition.py).
_JUNK_PREFIXES = ("步骤", "详见", "比例", "见步骤", "以上", "备注")
_JUNK_EXACT = {"配菜", "食材", "主料", "辅料", "其他", "适量"}


def _ranked_targets(col: str, top: int, mains_only: bool = True) -> list:
    """Ingredients missing this nutrient, ordered by total grams used in recipes.

    mains_only skips 调料: they are ~38% of recipe mass but a much smaller share
    of what you actually absorb (condiment_ratio discounts them), and spending
    the API budget on 生抽/老抽 instead of 一级农产品 is a poor trade.
    """
    cond_clause = "AND i.is_condiment = 0" if mains_only else ""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT i.name, SUM(i.amount) AS grams
            FROM ingredients i JOIN nutrition_cache n ON n.ingredient_name = i.name
            WHERE i.unit = 'g' AND n.{col} IS NULL {cond_clause}
            GROUP BY i.name
            ORDER BY grams DESC
        """).fetchall()
    finally:
        conn.close()
    out = [(r["name"], r["grams"]) for r in rows
           if r["name"] not in _JUNK_EXACT and not r["name"].startswith(_JUNK_PREFIXES)]
    return out[:top]


_PROMPT = """你是营养数据专家。给出下列食材每 100g 的{cn}含量（单位 {unit}）。

食材：{names}

只返回 JSON 数组，每项：{{"name": "食材名（原样返回）", "{key}": 数值}}

要求：
- 生鲜食材按生重计
- **确实不含该营养素 → 填 0**（例如蔬菜、水果、谷物的维生素D 就是 0，
  植物性食物的维生素B12 也是 0）。这是真实数据，不是猜测。
- **你无法确定、或查不到可靠来源 → 填 null**（不要为了填满而猜）
  这两种情况必须分开：0 表示"确定不含"，null 表示"不知道"。UI 靠这个区分
  "确实没吃到"和"没有数据"，混淆会让整条 DRI 进度条失去意义。
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
    ap.add_argument("--all-ingredients", action="store_true",
                    help="连调料一起补（默认只补主料/一级食材）")
    args = ap.parse_args()

    keys = [k.strip() for k in args.nutrient.split(",") if k.strip() in _NUTRIENTS]
    if not keys:
        print("没有有效的营养素名")
        return

    client = None
    for key in keys:
        col, unit, cn = _NUTRIENTS[key]
        targets = _ranked_targets(col, args.top, mains_only=not args.all_ingredients)
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
                    if nm and val is not None:   # 0 是有效值，只有 None 表示未知
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
