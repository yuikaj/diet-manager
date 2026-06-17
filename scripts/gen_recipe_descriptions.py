"""
scripts/gen_recipe_descriptions.py

Use Gemini to bulk-generate en_name / en_desc / zh_desc for recipes.
Results are written directly to the recipes DB table.

Usage:
    python3.9 scripts/gen_recipe_descriptions.py              # all recipes missing en_name
    python3.9 scripts/gen_recipe_descriptions.py --force      # re-generate even if filled
    python3.9 scripts/gen_recipe_descriptions.py --recipe 红烧肉   # single recipe (name match)
    python3.9 scripts/gen_recipe_descriptions.py --dry-run    # list targets, don't call API

Fields generated per recipe:
    en_name  — English menu name (4-8 words, elegant restaurant style)
    en_desc  — 1-sentence English tagline (≤20 words, sensory/evocative)
    zh_desc  — 1-sentence Chinese tagline (≤15 characters, optional)
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types

from dotenv import load_dotenv
load_dotenv()

from db.init_db import get_connection
from db.recipes import get_all_recipes, get_ingredients

_MODEL      = "gemini-flash-lite-latest"
_BATCH_SIZE = 12
_SLEEP_SECS = 12


# ── DB helpers ────────────────────────────────────────────────

def _update_descriptions(recipe_id: str, en_name: str, en_desc: str, zh_desc: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE recipes SET en_name=?, en_desc=?, zh_desc=? WHERE id=?",
            (en_name or None, en_desc or None, zh_desc or None, recipe_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Gemini prompt ─────────────────────────────────────────────

def _build_prompt(recipes: list[dict]) -> str:
    lines = []
    for r in recipes:
        ings  = r.get("_ingredients", [])
        main  = [i["name"] for i in ings if not i.get("is_condiment")][:6]
        conds = [i["name"] for i in ings if i.get("is_condiment")][:4]
        methods = ", ".join(r.get("cooking_method") or []) or "—"
        lines.append(
            f'id={r["id"]} | 菜名={r["name"]} | 主料={", ".join(main) or "—"}'
            + (f' | 调料={", ".join(conds)}' if conds else "")
            + f' | 烹饪={methods}'
        )
    recipes_text = "\n".join(lines)

    return f"""你是一位精通中餐英译的文案专家，专为高档家庭餐厅菜单撰写菜品英文描述。

请为以下每道菜生成3个字段：
1. en_name: 英文菜名，餐厅菜单风格，简洁优雅，4-8词。例：
   - "Braised Pork Belly with Red Fermented Tofu"
   - "Chilled Celery with Sesame & Sea Salt"
   - "Winter Melon & Edamame in Supreme Broth"
2. en_desc: 英文简介，1句话，15-20词，描述口感/食材/烹饪特色，感官化，优雅。例：
   - "Meltingly tender pork slow-braised to a glossy mahogany glaze, rich with umami."
   - "Crisp, cold, and impossibly refreshing — a minimalist triumph of texture."
3. zh_desc: 中文简介，1句话，40字以内，体现菜品特色，简洁。可留空字符串。例：
   - "肉质酥烂，汤汁浓郁"
   - "蒜末炸至金黄焦脆，裹住提前腌透的肋排。咬开酥壳，热气带着蒜香扑出来。"

返回合法JSON数组（无markdown包裹），每个元素格式：
{{"id": "...", "en_name": "...", "en_desc": "...", "zh_desc": "..."}}

数组长度必须和输入菜谱数量完全相同，顺序一致。

菜谱列表（共{len(recipes)}道）：
{recipes_text}"""


def _call_gemini(client, recipes: list[dict]) -> list[dict]:
    prompt = _build_prompt(recipes)
    resp = client.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
        ),
    )
    raw = resp.text.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate en_name/en_desc/zh_desc for recipes via Gemini")
    parser.add_argument("--recipe",   help="Recipe name substring (only process matching recipes)")
    parser.add_argument("--force",    action="store_true", help="Re-generate even if en_name already set")
    parser.add_argument("--dry-run",  action="store_true", help="List targets without calling API")
    parser.add_argument("--model",    default=_MODEL,      help=f"Gemini model (default: {_MODEL})")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: GEMINI_API_KEY not set"); sys.exit(1)

    all_recipes = get_all_recipes()
    if args.recipe:
        all_recipes = [r for r in all_recipes if args.recipe in r["name"]]
        if not all_recipes:
            print(f"No recipes matching '{args.recipe}'"); sys.exit(0)

    # Filter to those needing generation
    targets = [r for r in all_recipes if args.force or not r.get("en_name")]
    print(f"Recipes to process: {len(targets)}  (total={len(all_recipes)}, force={args.force})")

    if not targets:
        print("Nothing to do."); return

    if args.dry_run:
        for r in targets:
            print(f"  {r['name']}")
        return

    # Attach ingredients for context
    for r in targets:
        r["_ingredients"] = get_ingredients(r["id"])

    client = genai.Client(api_key=api_key)

    batches = [targets[i:i+_BATCH_SIZE] for i in range(0, len(targets), _BATCH_SIZE)]
    total_written = 0

    for batch_idx, batch in enumerate(batches):
        print(f"\nBatch {batch_idx+1}/{len(batches)} ({len(batch)} recipes)…")
        try:
            results = _call_gemini(client, batch)
        except Exception as e:
            print(f"  ERROR calling Gemini: {e}")
            if batch_idx < len(batches) - 1:
                time.sleep(_SLEEP_SECS)
            continue

        # Match results by id (Gemini returns them in order, but verify)
        result_map = {item["id"]: item for item in results if isinstance(item, dict)}

        for r in batch:
            item = result_map.get(r["id"])
            if not item:
                print(f"  ⚠  No result for {r['name']} ({r['id'][:8]}…)")
                continue
            en_name = (item.get("en_name") or "").strip()
            en_desc = (item.get("en_desc") or "").strip()
            zh_desc = (item.get("zh_desc") or "").strip()
            _update_descriptions(r["id"], en_name, en_desc, zh_desc)
            print(f"  ✓  {r['name']}")
            print(f"       {en_name}")
            print(f"       {en_desc}")
            if zh_desc:
                print(f"       {zh_desc}")
            total_written += 1

        if batch_idx < len(batches) - 1:
            print(f"  sleeping {_SLEEP_SECS}s…")
            time.sleep(_SLEEP_SECS)

    print(f"\n✅ Done. Wrote descriptions for {total_written} recipes.")


if __name__ == "__main__":
    main()
