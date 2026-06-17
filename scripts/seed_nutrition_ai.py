"""
scripts/seed_nutrition_ai.py

Scan recipe ingredients → find those missing from the nutrition DB →
ask Gemini (with Google Search grounding) for per-100g nutrition facts →
write results to data/local_nutrition.json.

Usage:
    python3.9 scripts/seed_nutrition_ai.py                          # all recipes
    python3.9 scripts/seed_nutrition_ai.py --recipe 红烧肉          # recipe by name (substring match)
    python3.9 scripts/seed_nutrition_ai.py --recipe <uuid>          # recipe by ID
    python3.9 scripts/seed_nutrition_ai.py --ingredient 草莓        # one specific ingredient
    python3.9 scripts/seed_nutrition_ai.py --ingredient 草莓 --ingredient 蓝莓  # multiple
    python3.9 scripts/seed_nutrition_ai.py --force                  # re-query cached items too
    python3.9 scripts/seed_nutrition_ai.py --dry-run                # list missing, don't query
    python3.9 scripts/seed_nutrition_ai.py --condiments             # include condiments
    python3.9 scripts/seed_nutrition_ai.py --clear-cache            # also invalidate DB cache for updated items
    python3.9 scripts/seed_nutrition_ai.py --manual-update 草莓 --data "每份100g含热量32千卡..."
    python3.9 scripts/seed_nutrition_ai.py --manual-update 草莓 --data "$(pbpaste)" --clear-cache

Notes:
  - Grounding requires google-genai >= 1.0; incompatible with response_mime_type=json.
  - Rate: ~6 s sleep between batches of 15 → stays well under free-tier RPM limits.
  - To rescan a newly added recipe: --recipe <id>
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

from config import DATA_DIR
from db.nutrition import get_all_cached_names, invalidate_cache
from db.recipes import get_all_recipes, get_ingredients

_LOCAL_PATH   = DATA_DIR / "local_nutrition.json"
_BATCH_SIZE   = 15
_SLEEP_SECS   = 15    # between batches

_FIELDS = [
    "kcal", "protein", "fat", "carbs",
    "sodium", "fiber",
    "vitc", "iron", "calcium", "potassium",
    "vitd", "vita", "magnesium", "zinc",
]

_FIELD_UNITS = (
    "kcal=千卡; protein/fat/carbs/fiber=克; "
    "sodium/vitc/iron/calcium/potassium/magnesium/zinc=毫克; "
    "vitd=微克; vita=微克RAE"
)


# ── JSON helpers ─────────────────────────────────────────────────

def _extract_json(text: str):
    """Extract first JSON array or object from a string."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in (r'\[.*\]', r'\{.*\}'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No valid JSON in response:\n{text[:400]}")


# ── local_nutrition.json I/O ─────────────────────────────────────

def _load_local() -> dict:
    try:
        with open(_LOCAL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"_comment": "Local nutrition fallback. All values per 100g."}


def _save_local(data: dict) -> None:
    """Write local_nutrition.json — _comment first, entries sorted."""
    ordered = {}
    if "_comment" in data:
        ordered["_comment"] = data["_comment"]
    for k in sorted(data):
        if not k.startswith("_"):
            ordered[k] = data[k]
    with open(_LOCAL_PATH, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)


# ── Gemini calls ─────────────────────────────────────────────────

def _build_manual_prompt(name: str, raw_data: str) -> str:
    return f"""你是专业营养数据库编辑。用户提供了关于「{name}」的参考资料，请从中提取每100g可食部的营养数据。

参考资料：
{raw_data}

要求：
- 如果资料按某份量标注（如每份30g），请换算为每100g的数值
- 数值不确定或资料中未提及时填 null
- 在 note 字段简要说明来源和换算方法

返回单个 JSON 对象：
{{
  "name": "{name}",
  "en_name": "English name",
  "per_100g": {{
    "kcal": 数值, "protein": 数值, "fat": 数值, "carbs": 数值,
    "sodium": 数值, "fiber": 数值,
    "vitc": 数值, "iron": 数值, "calcium": 数值, "potassium": 数值,
    "vitd": 数值, "vita": 数值, "magnesium": 数值, "zinc": 数值
  }},
  "source": "manual_reference",
  "note": "来源说明"
}}

单位说明：{_FIELD_UNITS}
只返回 JSON 对象，不要任何解释文字。
"""


def _query_manual(client, name: str, raw_data: str, model: str) -> dict:
    prompt = _build_manual_prompt(name, raw_data)
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            result = _extract_json(resp.text)
            if isinstance(result, list):
                result = result[0]   # model wrapped it in an array
            return result
        except Exception as exc:
            if attempt < 2:
                print(f"    ⚠️  重试 ({attempt+1}/3): {exc}")
                time.sleep(5)
            else:
                raise


def _build_prompt(names: list) -> str:
    names_str = "\n".join(f"- {n}" for n in names)
    return f"""你是专业营养数据库编辑。请查找以下中文食材的精确营养数据（每100g可食部）。

食材列表：
{names_str}

查找规则：
1. 优先匹配 USDA FoodData Central 中最接近的「新鲜/生/原始」状态条目（不要匹配加工品、罐头、脱水粉末）。
2. 如果 USDA 无合适条目，请搜索中国食物成分表或其他权威营养数据库。
3. 数值不确定时在 note 中注明，字段值用 null。

返回一个严格的 JSON 数组，每个元素格式如下（如某字段数据缺失，值填 null）：
[
  {{
    "name": "食材名（与输入一致）",
    "en_name": "English name",
    "per_100g": {{
      "kcal": 数值, "protein": 数值, "fat": 数值, "carbs": 数值,
      "sodium": 数值, "fiber": 数值,
      "vitc": 数值, "iron": 数值, "calcium": 数值, "potassium": 数值,
      "vitd": 数值, "vita": 数值, "magnesium": 数值, "zinc": 数值
    }},
    "source": "usda" | "china_food_composition" | "web_estimate",
    "note": "参考条目说明，如 USDA FDC 170069"
  }}
]

单位说明：{_FIELD_UNITS}
只返回 JSON 数组，不要任何解释文字。
"""


def _query_batch(client, names: list, model: str) -> list:
    prompt = _build_prompt(names)
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.1,
                ),
            )
            return _extract_json(resp.text)
        except Exception as exc:
            if attempt < 2:
                print(f"    ⚠️  重试 ({attempt+1}/3): {exc}")
                time.sleep(5)
            else:
                raise


# ── Missing-ingredient collection ────────────────────────────────

def _collect_missing(
    recipe_ids: list,
    include_condiments: bool,
    cached: set,
    local_keys: set,
    force: bool,
) -> list:
    names: set = set()
    for rid in recipe_ids:
        for ing in get_ingredients(rid):
            if not include_condiments and ing.get("is_condiment"):
                continue
            name = (ing.get("name") or "").strip()
            if not name:
                continue
            if not force and (name in cached or name in local_keys):
                continue
            names.add(name)
    return sorted(names)


# ── Recipe resolver ─────────────────────────────────────────────

def _resolve_recipe(query: str) -> tuple:
    """Accept a UUID or a recipe name substring → (list[id], display_label)."""
    import re as _re
    all_r = get_all_recipes()
    if _re.fullmatch(r'[0-9a-f\-]{36}', query):
        return [query], query
    matches = [r for r in all_r if query in r["name"]]
    if not matches:
        print(f"❌  未找到菜名包含「{query}」的菜谱")
        sys.exit(1)
    if len(matches) > 1:
        print(f"⚠️  匹配到 {len(matches)} 道菜谱，全部扫描：")
        for r in matches:
            print(f"   - {r['name']}  ({r['id']})")
    return [r["id"] for r in matches], " / ".join(r["name"] for r in matches)


# ── Entry point ──────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Seed nutrition facts via Gemini AI")
    ap.add_argument("--recipe",         help="Recipe name (substring) or UUID — scan its ingredients")
    ap.add_argument("--ingredient",     action="append", metavar="NAME",
                    help="Add a specific ingredient (can repeat: --ingredient 草莓 --ingredient 蓝莓)")
    ap.add_argument("--manual-update",  metavar="INGREDIENT",
                    help="Manually correct one ingredient using --data text (no web search)")
    ap.add_argument("--data",           metavar="TEXT",
                    help="Raw reference text for --manual-update (paste nutrition label / search result)")
    ap.add_argument("--force",          action="store_true", help="Re-query cached items")
    ap.add_argument("--dry-run",        action="store_true", help="List missing, don't query")
    ap.add_argument("--condiments",     action="store_true", help="Include condiment ingredients")
    ap.add_argument("--clear-cache",    action="store_true", help="Invalidate DB cache for updated items")
    ap.add_argument("--model",          default="gemini-2.5-flash",
                    help="Gemini model (default: 2.5-flash — 25 RPD free)")
    args = ap.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌  GEMINI_API_KEY not set"); sys.exit(1)

    client     = genai.Client(api_key=api_key)
    local_data = _load_local()
    cached     = set(get_all_cached_names())
    local_keys = {k for k in local_data if not k.startswith("_")}

    # ── manual-update mode ───────────────────────────────────────
    if args.manual_update:
        name = args.manual_update.strip()
        raw  = (args.data or "").strip()
        if not raw:
            print("❌  --manual-update 需要同时提供 --data \"参考文字\"")
            sys.exit(1)
        print(f"✏️  手动更新：「{name}」")
        print(f"   参考资料（前200字）：{raw[:200]}…\n")
        try:
            entry = _query_manual(client, name, raw, args.model)
        except Exception as exc:
            print(f"❌  解析失败: {exc}"); sys.exit(1)

        per_100g_raw = entry.get("per_100g") or {}
        per_100g = {f: (float(v) if v is not None else None) for f, v in
                    ((f, per_100g_raw.get(f)) for f in _FIELDS)}

        local_data[name] = {
            "en_name": entry.get("en_name", ""),
            "per_100g": per_100g,
            "source":   entry.get("source") or "manual_reference",
            "note":     entry.get("note", ""),
        }
        _save_local(local_data)
        print(f"✅  已写入 local_nutrition.json")
        print(f"   kcal={per_100g.get('kcal')}  protein={per_100g.get('protein')}g  "
              f"sodium={per_100g.get('sodium')}mg")
        if name in cached or args.clear_cache:
            invalidate_cache(name)
            print(f"🗑️  DB 缓存已清除，下次查询将使用新数据")
        else:
            print(f"💡  提示：若该食材已在 DB 缓存中，用 --clear-cache 清除旧缓存")
        return

    # ── ingredient / recipe selection ────────────────────────────
    explicit = [n.strip() for n in (args.ingredient or []) if n.strip()]

    if explicit and not args.recipe:
        recipe_ids = []
        print(f"🧪  指定食材模式: {', '.join(explicit)}")
    elif args.recipe:
        recipe_ids, label = _resolve_recipe(args.recipe)
        print(f"🎯  单菜谱模式: {label}")
    else:
        recipe_ids = [r["id"] for r in get_all_recipes()]
        print(f"📦  全菜谱模式: {len(recipe_ids)} 道")

    missing_set = set(_collect_missing(
        recipe_ids,
        include_condiments=args.condiments,
        cached=cached,
        local_keys=local_keys,
        force=args.force,
    ))

    # merge explicit --ingredient items
    for name in explicit:
        if args.force or (name not in cached and name not in local_keys):
            missing_set.add(name)
        else:
            print(f"ℹ️  「{name}」已有数据，跳过（用 --force 强制重新查询）")

    missing = sorted(missing_set)

    if not missing:
        print("✅  所有食材已有营养数据，无需查询。")
        return

    print(f"🔍  发现 {len(missing)} 种食材需要查询\n")

    if args.dry_run:
        for n in missing:
            status = "cached" if n in cached else ("local" if n in local_keys else "missing")
            print(f"  {'🔄' if args.force else '❓'}  {n}  ({status})")
        return

    # ── batch processing ─────────────────────────────────────────
    batches    = [missing[i:i+_BATCH_SIZE] for i in range(0, len(missing), _BATCH_SIZE)]
    total_ok   = 0
    total_fail = 0

    for bi, batch in enumerate(batches, 1):
        print(f"🤖  批次 {bi}/{len(batches)} — {len(batch)} 种食材：{', '.join(batch)}")

        try:
            results = _query_batch(client, batch, args.model)
        except Exception as exc:
            print(f"   ❌  批次失败: {exc}")
            total_fail += len(batch)
            continue

        if not isinstance(results, list):
            results = [results]   # model returned a single object

        found: set = set()
        for entry in results:
            name = (entry.get("name") or "").strip()
            if not name:
                continue

            per_100g_raw = entry.get("per_100g") or {}
            per_100g = {}
            for field in _FIELDS:
                v = per_100g_raw.get(field)
                per_100g[field] = float(v) if v is not None else None

            local_data[name] = {
                "en_name": entry.get("en_name", ""),
                "per_100g": per_100g,
                "source":   entry.get("source", "gemini_search"),
                "note":     entry.get("note", ""),
            }
            found.add(name)

            kcal_str = f"{per_100g.get('kcal')} kcal" if per_100g.get("kcal") else "? kcal"
            prot_str = f"{per_100g.get('protein')}g 蛋白" if per_100g.get("protein") else ""
            print(f"   ✅  {name} ({entry.get('en_name', '')}) — {kcal_str}  {prot_str}")

            if args.clear_cache and name in cached:
                invalidate_cache(name)
                print(f"       🗑️  DB 缓存已清除: {name}")

        for n in set(batch) - found:
            print(f"   ⚠️  {n}: Gemini 未返回结果，跳过")
            total_fail += 1

        total_ok += len(found)
        _save_local(local_data)
        print(f"   💾  已写入 {_LOCAL_PATH}\n")

        if bi < len(batches):
            time.sleep(_SLEEP_SECS)

    print("=" * 50)
    print(f"✅  完成  成功: {total_ok}  失败/缺失: {total_fail}")
    print(f"📄  {_LOCAL_PATH}")
    if total_ok and not args.clear_cache:
        print("💡  提示：已在 local_nutrition.json 中的新条目会在下次查询时被写入 DB 缓存。")
        print("    若某食材已有错误的 DB 缓存，用 --clear-cache 或在 UI「营养分析 → 管理」页删除。")


if __name__ == "__main__":
    main()
