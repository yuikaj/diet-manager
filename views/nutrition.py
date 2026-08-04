"""Phase 4 / 7: USDA-powered nutrition analysis + full-day tracking."""
import json
import os
import re
from datetime import datetime
from typing import Optional

import streamlit as st

import pandas as pd

from db.init_db import get_connection
from db.daily_log import save_daily_log, get_recent_logs, get_recent_logs_full
from db.nutrition import get_all_cached_names, invalidate_cache, update_cached_nutrients
from db.recipes import get_ingredients, get_recipe
from utils.cache import get_all_recipes_cached as get_all_recipes
from utils.nutrition_lookup import (
    MealNutrition, NutritionPer100g,
    lookup_ingredient, to_grams, calc_nutrition_with_breakdown,
)

# ── Daily Reference Intake — per person / day ─────────────────
_DRI = {
    "kcal":       2000.0,   # kcal
    "protein":      50.0,   # g   (overridden by user weight × multiplier)
    "fat":          65.0,   # g
    "carbs":       300.0,   # g
    "sodium":     2300.0,   # mg  (upper limit — not a target)
    "fiber":        25.0,   # g
    "vitc":         90.0,   # mg
    "iron":         10.0,   # mg
    "calcium":    1000.0,   # mg
    "potassium":  4700.0,   # mg
    "vitd":         15.0,   # µg
    "vita":        800.0,   # µg RAE
    "magnesium":   350.0,   # mg
    "zinc":         10.0,   # mg
}

_SODIUM_WARN_PER_PERSON  = 1500.0   # mg — yellow warning (dinner alone)
_SODIUM_LIMIT_PER_PERSON = 2300.0   # mg — red alert


# ── Helpers ───────────────────────────────────────────────────

def _protein_target() -> tuple:
    """Return (min_g, max_g) combined protein target for 2 people."""
    try:
        conn = get_connection()
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM user_settings"
        ).fetchall()}
        conn.close()
        wa  = float(rows.get("weight_a", 65))
        wb  = float(rows.get("weight_b", 55))
        mn  = float(rows.get("protein_multiplier_min", 1.2))
        mx  = float(rows.get("protein_multiplier_max", 1.5))
        return (wa + wb) * mn, (wa + wb) * mx
    except Exception:
        return 144.0, 180.0


def _parse_placeholder(text: str) -> list:
    """Parse free-form text into ingredient dicts.
    Accepted format (one per line):  食材名  数量  [单位]
    """
    result = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(.+?)\s+([\d.]+)\s*([a-zA-Z一-鿿]+)?', line)
        if not m:
            continue
        name = m.group(1).strip()
        try:
            amount = float(m.group(2))
        except ValueError:
            continue
        unit = (m.group(3) or "g").strip()
        result.append({"name": name, "amount": amount, "unit": unit, "intake_ratio": 1.0})
    return result


# ── Display components ────────────────────────────────────────

def _bar(label: str, value: float, dri_1p: float, unit: str,
         warn_over: bool = False) -> None:
    """One nutrient progress bar — value and DRI both per-person."""
    if dri_1p > 0:
        pct      = value / dri_1p
        pct_disp = pct * 100
    else:
        pct = pct_disp = 0.0

    warn_icon = " ⚠️" if warn_over and pct > 0.65 else (
                " 🚨" if warn_over and pct > 1.0 else "")

    bc, vc = st.columns([5, 2])
    bc.progress(min(pct, 1.0), text=f"{label}：**{value:.1f} {unit}**{warn_icon}")
    vc.caption(f"{pct_disp:.0f}% DRI/人")


def _results(nutr: MealNutrition, breakdown: list) -> None:
    """Render complete nutrition results panel (all values per-person)."""
    p_min, p_max = _protein_target()
    pp = 2.0   # recipes sized for 2 — divide totals to get per-person

    kcal_pp   = nutr.kcal      / pp
    prot_pp   = nutr.protein   / pp
    fat_pp    = nutr.fat       / pp
    carbs_pp  = nutr.carbs     / pp
    sodium_pp = nutr.sodium    / pp
    fiber_pp  = nutr.fiber     / pp
    vitc_pp   = nutr.vitc      / pp
    iron_pp   = nutr.iron      / pp
    cal_pp    = nutr.calcium   / pp
    pot_pp    = nutr.potassium / pp
    vitd_pp   = nutr.vitd      / pp
    vita_pp   = nutr.vita      / pp
    mag_pp    = nutr.magnesium / pp
    zinc_pp   = nutr.zinc      / pp

    p_min_pp = p_min / pp
    p_max_pp = p_max / pp

    # Top metrics (per-person)
    mc = st.columns(4)
    mc[0].metric("热量 / 人",   f"{kcal_pp:.0f} kcal")
    mc[1].metric("蛋白质 / 人", f"{prot_pp:.1f} g")
    mc[2].metric("脂肪 / 人",   f"{fat_pp:.1f} g")
    mc[3].metric("碳水 / 人",   f"{carbs_pp:.1f} g")

    # Protein target
    if prot_pp < p_min_pp:
        st.warning(f"🥩 蛋白质不足：{prot_pp:.1f}g/人，目标 {p_min_pp:.0f}–{p_max_pp:.0f}g（缺口 {p_min_pp-prot_pp:.0f}g）")
    elif prot_pp <= p_max_pp:
        st.success(f"✅ 蛋白质达标：{prot_pp:.1f}g/人（目标 {p_min_pp:.0f}–{p_max_pp:.0f}g）")
    else:
        st.info(f"蛋白质充裕：{prot_pp:.1f}g/人（目标上限 {p_max_pp:.0f}g）")

    if nutr.missing:
        st.warning(
            f"⚠️ {len(nutr.missing)} 种食材未查到数据（已跳过）：**{', '.join(nutr.missing)}**\n\n"
            "→ 可在「🔍 食材查询」或「🗄️ 食材库」标签页修正"
        )

    st.divider()

    # Macro bars
    st.subheader("Macros（每人 / 晚餐）")
    st.caption("与每人每日 DRI 对比（晚餐理想约 35–40%）")
    _bar("热量",   kcal_pp,  _DRI["kcal"],    "kcal")
    # 【修复】使用动态算出的单人蛋白质最低目标 p_min_pp
    _bar("蛋白质", prot_pp,  p_min_pp,        "g")
    _bar("脂肪",   fat_pp,   _DRI["fat"],     "g")
    _bar("碳水",   carbs_pp, _DRI["carbs"],   "g")

    st.divider()

    # Micro bars
    st.subheader("Micros（每人 / 晚餐）")
    _bar("钠",     sodium_pp, _DRI["sodium"],    "mg", warn_over=True)
    if sodium_pp > _SODIUM_LIMIT_PER_PERSON:
        st.error(f"🚨 钠严重超标：每人 {sodium_pp:.0f}mg，超过全天上限 {_SODIUM_LIMIT_PER_PERSON:.0f}mg！建议减少调料用量或调整菜谱调料摄入比例。")
    elif sodium_pp > _SODIUM_WARN_PER_PERSON:
        st.warning(f"⚠️ 钠偏高：每人 {sodium_pp:.0f}mg，叠加早午餐可能超出全天上限。")

    _bar("膳食纤维", fiber_pp, _DRI["fiber"],     "g")
    _bar("维生素C",  vitc_pp,  _DRI["vitc"],      "mg")
    _bar("铁",       iron_pp,  _DRI["iron"],      "mg")
    _bar("钙",       cal_pp,   _DRI["calcium"],   "mg")
    _bar("钾",       pot_pp,   _DRI["potassium"], "mg")
    _bar("维生素D",  vitd_pp,  _DRI["vitd"],      "µg")
    _bar("维生素A",  vita_pp,  _DRI["vita"],      "µg")
    _bar("镁",       mag_pp,   _DRI["magnesium"], "mg")
    _bar("锌",       zinc_pp,  _DRI["zinc"],      "mg")

    # Ingredient breakdown
    st.divider()
    with st.expander(f"📋 食材明细（{nutr.found} 种成功 · {len(nutr.missing)} 种缺失）"):
        if breakdown:
            st.dataframe(
                breakdown,
                column_config={"USDA": st.column_config.LinkColumn("USDA", display_text="🔗")},
                use_container_width=True,
                hide_index=True,
            )


# ── Tab 1: 菜谱营养计算 ───────────────────────────────────────

def _tab_calc() -> None:
    mode = st.radio(
        "输入方式",
        ["📚 从菜谱库选择", "📅 从今日规划", "✏️ 临时输入食材 (Placeholder)"],
        horizontal=True,
        label_visibility="collapsed",
    )

    ingredients: list = []

    if mode == "📚 从菜谱库选择":
        recipes = get_all_recipes()
        if not recipes:
            st.info(
                "菜谱库为空，请先在「🍳 菜谱库」添加菜谱，"
                "或切换到「临时输入食材」直接输入。"
            )
            return

        opts = {r["name"]: r["id"] for r in recipes}
        sel  = st.multiselect(
            "选择菜谱（可多选，支持今晚全套菜单）",
            list(opts.keys()),
            placeholder="选择今晚要做的菜…",
        )
        if not sel:
            st.caption("选择菜谱后点击「开始计算」")
            return

        for name in sel:
            rid    = opts[name]
            recipe = get_recipe(rid)
            cond_r = float((recipe or {}).get("condiment_ratio") or 1.0)
            for ing in get_ingredients(rid):
                ratio = cond_r if ing.get("is_condiment") else 1.0
                ingredients.append({
                    "name":         ing["name"],
                    "amount":       float(ing.get("amount") or 0),
                    "unit":         ing.get("unit", "g"),
                    "intake_ratio": ratio,
                })
        calc_label = " + ".join(sel)

    elif mode == "📅 从今日规划":
        rids = st.session_state.get("plan_rids", [])
        if not rids:
            st.info("今日规划为空，请先在「📅 今日规划」页选定菜谱，再回来计算。")
            return
        names: list = []
        for rid in rids:
            recipe = get_recipe(rid)
            if recipe is None:
                continue
            cond_r = float(recipe.get("condiment_ratio") or 1.0)
            names.append(recipe["name"])
            for ing in get_ingredients(rid):
                ratio = cond_r if ing.get("is_condiment") else 1.0
                ingredients.append({
                    "name":         ing["name"],
                    "amount":       float(ing.get("amount") or 0),
                    "unit":         ing.get("unit", "g"),
                    "intake_ratio": ratio,
                })
        if not ingredients:
            st.warning("今日规划中的菜谱未找到食材数据。")
            return
        calc_label = " + ".join(names)
        st.caption(f"已加载今日规划：**{calc_label}**")

    else:  # Placeholder
        st.caption(
            "每行一种食材，格式：`食材名  用量  单位`  \n"
            "示例：`猪五花  300  g`  |  `鸡腿  400  g`  |  `生抽  15  ml`"
        )
        raw = st.text_area(
            "食材列表",
            height=150,
            placeholder="猪五花  300  g\n西蓝花  250  g\n生抽  15  ml\n蚝油  10  g",
            label_visibility="collapsed",
        )
        if not raw.strip():
            return
        ingredients = _parse_placeholder(raw)
        calc_label  = "临时食材输入"
        if not ingredients:
            st.warning("未能解析任何食材，请检查格式：`食材名  数量  单位`（中间用空格分隔）")
            return
        st.caption(f"已解析 {len(ingredients)} 种食材")

    if st.button("🔍 开始计算", type="primary", use_container_width=True):
        with st.spinner("正在查询营养数据（首次需联网查询 USDA，后续走本地缓存）…"):
            nutr, bd = calc_nutrition_with_breakdown(ingredients)
        st.session_state["_n_nutr"] = nutr
        st.session_state["_n_bd"]   = bd
        st.session_state["_n_lbl"]  = calc_label

    if "_n_nutr" in st.session_state:
        st.divider()
        st.markdown(f"**菜单**：{st.session_state['_n_lbl']}")
        _results(st.session_state["_n_nutr"], st.session_state["_n_bd"])


# ── Tab 2: 食材快速查询 ───────────────────────────────────────

def _tab_lookup() -> None:
    st.subheader("单项食材营养查询（每 100g）")
    st.caption("支持中文名。命中后自动写入本地 SQLite 缓存，下次离线可用。")

    c1, c2, c3 = st.columns([3, 1, 1])
    ing_input     = c1.text_input(
        "食材名",
        placeholder="如：猪五花 / 西蓝花 / 三文鱼",
        label_visibility="collapsed",
    )
    force_refresh = c2.checkbox("强制刷新", help="忽略缓存，重新查 USDA")
    do_search     = c3.button("查询", use_container_width=True)

    if do_search and ing_input.strip():
        name = ing_input.strip()
        with st.spinner(f"查询 {name}…"):
            result = lookup_ingredient(name, force_refresh=force_refresh)

        if result is None:
            st.error(
                f"未找到「{name}」。  \n"
                "可能原因：① `ingredient_translations.json` 缺少此食材的英文映射  "
                "② USDA 无此条目  ③ 网络问题  \n"
                "解决：在 `data/ingredient_translations.json` 添加 `\"中文名\": \"english name\"` 映射后重试。"
            )
        else:
            src_label = {
                "usda":           "🌐 USDA FoodData Central",
                "local":          "📄 local_nutrition.json",
                "manual_estimate":"✏️ 手动估算",
            }.get(result.source, f"💾 {result.source}")

            st.success(f"**{name}** — 来源：{src_label}")
            if result.food_name and result.food_name.lower() != name.lower():
                st.caption(f"匹配英文名：{result.food_name}")

            r1 = st.columns(5)
            r1[0].metric("热量",   f"{result.kcal or 0:.1f} kcal")
            r1[1].metric("蛋白质", f"{result.protein or 0:.1f} g")
            r1[2].metric("脂肪",   f"{result.fat or 0:.1f} g")
            r1[3].metric("碳水",   f"{result.carbs or 0:.1f} g")
            r1[4].metric("钠",     f"{result.sodium or 0:.1f} mg")

            r2 = st.columns(5)
            r2[0].metric("膳食纤维", f"{result.fiber or 0:.1f} g")
            r2[1].metric("维生素C",  f"{result.vitc or 0:.1f} mg")
            r2[2].metric("铁",       f"{result.iron or 0:.1f} mg")
            r2[3].metric("钙",       f"{result.calcium or 0:.1f} mg")
            r2[4].metric("钾",       f"{result.potassium or 0:.1f} mg")

            st.caption("以上为每 100g 含量")

            if st.button("🗑️ 清除此缓存"):
                invalidate_cache(name)
                st.success(f"「{name}」缓存已清除，下次查询将重新请求 USDA")
                st.rerun()

    st.divider()
    st.subheader("已缓存食材")
    cached = get_all_cached_names()
    if not cached:
        st.info("暂无缓存。查询食材后自动写入，提升下次速度，支持离线使用。")
        return

    st.caption(f"共 {len(cached)} 种食材（永久有效，无需重复联网）")
    n = 4
    for chunk in [cached[i:i+n] for i in range(0, len(cached), n)]:
        cols = st.columns(n)
        for j, name in enumerate(chunk):
            cols[j].caption(f"• {name}")


# ── Tab 3: 食材库 ─────────────────────────────────────────────

_COL_TO_KEY = {
    "kcal_per_100g":      "kcal",
    "protein_per_100g":   "protein",
    "fat_per_100g":       "fat",
    "carbs_per_100g":     "carbs",
    "sodium_per_100g":    "sodium",
    "fiber_per_100g":     "fiber",
    "vitc_per_100g":      "vitc",
    "iron_per_100g":      "iron",
    "calcium_per_100g":   "calcium",
    "potassium_per_100g": "potassium",
    "vitd_per_100g":      "vitd",
    "vita_per_100g":      "vita",
    "magnesium_per_100g": "magnesium",
    "zinc_per_100g":      "zinc",
    "en_name":            "en_name",
}

_COL_LABELS = {
    "ingredient_name":    "食材名",
    "en_name":            "英文名",
    "source":             "来源",
    "kcal_per_100g":      "热量(kcal)",
    "protein_per_100g":   "蛋白质(g)",
    "fat_per_100g":       "脂肪(g)",
    "carbs_per_100g":     "碳水(g)",
    "sodium_per_100g":    "钠(mg)",
    "fiber_per_100g":     "纤维(g)",
    "vitc_per_100g":      "维C(mg)",
    "iron_per_100g":      "铁(mg)",
    "calcium_per_100g":   "钙(mg)",
    "potassium_per_100g": "钾(mg)",
    "vitd_per_100g":      "维D(µg)",
    "vita_per_100g":      "维A(µg)",
    "magnesium_per_100g": "镁(mg)",
    "zinc_per_100g":      "锌(mg)",
}


def _sync_local_to_cache() -> int:
    """Bulk-load all local_nutrition.json entries into nutrition_cache. Returns newly added count."""
    import json as _json
    from config import DATA_DIR
    from db.nutrition import save_to_cache
    local_path = DATA_DIR / "local_nutrition.json"
    try:
        with open(local_path, encoding="utf-8") as f:
            local = _json.load(f)
    except FileNotFoundError:
        return 0
    cached = set(get_all_cached_names())
    count = 0
    _fields = ["kcal", "protein", "fat", "carbs", "sodium", "fiber",
               "vitc", "iron", "calcium", "potassium", "vitd", "vita", "magnesium", "zinc"]
    for name, entry in local.items():
        if name.startswith("_") or name in cached:
            continue
        p = entry.get("per_100g") or {}
        save_to_cache(
            ingredient_name=name,
            en_name=entry.get("en_name"),
            usda_food_id=f"local_{name}",
            nutrients={f: p.get(f) for f in _fields},
            source=entry.get("source") or "local",
        )
        count += 1
    return count


_NUTR_FIELDS = ["kcal", "protein", "fat", "carbs", "sodium", "fiber",
                "vitc", "iron", "calcium", "potassium",
                "vitd", "vita", "magnesium", "zinc"]


def _ai_query_nutrition(raw_input: str, use_grounding: bool = True) -> list:
    """Extract per-100g nutrition data from free-form text.

    use_grounding=True  → gemini-2.5-flash + Google Search (for food names only, 25 RPD)
    use_grounding=False → gemini-flash-lite-latest, no tools (for pasted raw data, high quota)

    Returns list of dicts: {name, en_name, kcal, ..., zinc, source_note}.
    """
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未设置")

    from google import genai
    from google.genai import types

    prompt = f"""你是专业营养数据库编辑。处理下面的输入，提取每 100g 可食部的精确营养数据。

【用户输入】
{raw_input}

━━━━━━━━━━━━━━━━━━━━━━━━━━
【输入识别】两种形式都支持，自行判断：

**A) 仅食材名清单**（如「黄豆酱、冬瓜、芥兰苗」或换行/逗号分隔）
→ 使用 Google 搜索查找权威营养数据：
   1. 优先匹配 USDA FoodData Central 的「新鲜/生/原始」条目（不要罐头/脱水粉/加工品）
   2. 若 USDA 无合适条目，搜索中国食物成分表第 6 版或其他权威数据库
   3. 数值确实查不到则填 null（不要瞎编）

**B) 已含营养数据原文**（如从网页/书复制的成分表，带具体数值）
→ 直接解析原文中的数值，**不必联网搜索**（节省 quota）
   - 单位换算：千焦→千卡（÷4.184）、毫克↔克 都要对齐到目标单位
   - 原文若没有某营养素的值，填 null
   - 一段原文通常对应一个食材，按食材数返回 JSON 元素

━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出 JSON 数组】每个元素：
{{
  "name": "食材标准中文名",
  "en_name": "English name 或 null",
  "kcal": 数值, "protein": 数值, "fat": 数值, "carbs": 数值,
  "sodium": 数值, "fiber": 数值,
  "vitc": 数值, "iron": 数值, "calcium": 数值, "potassium": 数值,
  "vitd": 数值, "vita": 数值, "magnesium": 数值, "zinc": 数值,
  "source_note": "数据来源说明（如 USDA FDC 170069 / 中国食物成分表第6版 / 解析自原文）"
}}

单位：protein/fat/carbs/fiber=g, sodium/vitc/iron/calcium/potassium/magnesium/zinc=mg, vitd/vita=µg, kcal=kcal。
只返回 JSON 数组，不要任何解释文字或 markdown 包裹。
"""
    client = genai.Client(api_key=api_key)
    if use_grounding:
        cfg = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
        )
        model = "gemini-2.5-flash"
    else:
        # No tool calls → can use high-quota flash-lite
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )
        model = "gemini-flash-lite-latest"
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=cfg,
    )
    text = resp.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # extract first JSON array (grounding sometimes adds prose before)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def _ai_nutrition_expander() -> None:
    """⚡ AI 录入营养数据 — paste names → Gemini lookup → preview & commit."""
    # Auto-expand if there's pending preview state or a sticky error to show
    has_state = ("ai_nutr_parsed" in st.session_state
                 or "ai_nutr_error" in st.session_state)
    with st.expander("⚡ AI 录入营养数据（粘贴食材名，Gemini 联网查询每 100g 营养）",
                     expanded=has_state):
        from db.nutrition import save_to_cache

        # Display sticky error from previous run (if any)
        if err := st.session_state.get("ai_nutr_error"):
            st.error(err)
            if st.button("✕ 关闭错误提示", key="ai_nutr_err_dismiss"):
                st.session_state.pop("ai_nutr_error", None)
                st.rerun()

        # ── Step 2: preview & commit ──────────────────────────
        if "ai_nutr_parsed" in st.session_state:
            parsed = st.session_state["ai_nutr_parsed"]
            cached_names = set(get_all_cached_names())

            for row in parsed:
                row["状态"] = "🟢 覆盖" if row.get("name") in cached_names else "🔵 新建"

            col_order = ["状态", "name", "en_name", "source_note"] + _NUTR_FIELDS
            df = pd.DataFrame(parsed).reindex(columns=col_order)

            st.caption(
                "✏️ 数值可在表格中直接编辑（数据来源仅供参考）。"
                "🟢 = 缓存已存在（将覆盖）；🔵 = 新建条目。"
            )
            edited = st.data_editor(
                df, use_container_width=True, hide_index=True,
                disabled=["状态", "name", "source_note"],
                num_rows="fixed",
                key="ai_nutr_editor",
            )

            bc1, bc2 = st.columns(2)
            if bc2.button("✅ 入库", type="primary", use_container_width=True):
                saved = 0
                for _, row in edited.iterrows():
                    name = str(row["name"])
                    nutrients = {}
                    for k in _NUTR_FIELDS:
                        v = row.get(k)
                        nutrients[k] = None if v is None or pd.isna(v) else float(v)
                    en_name = row.get("en_name")
                    if en_name is None or (isinstance(en_name, float) and pd.isna(en_name)):
                        en_name = None
                    else:
                        en_name = str(en_name)
                    save_to_cache(
                        ingredient_name=name,
                        en_name=en_name,
                        usda_food_id=f"ai_manual_{name}",
                        nutrients=nutrients,
                        source="ai_manual",
                    )
                    saved += 1
                st.session_state.pop("ai_nutr_parsed", None)
                st.success(f"✅ 已入库 {saved} 条食材营养")
                st.rerun()
            if bc1.button("← 重新输入", use_container_width=True):
                st.session_state.pop("ai_nutr_parsed", None)
                st.rerun()
            return

        # ── Step 1: text input ────────────────────────────────
        mode = st.radio(
            "模式",
            ["🔍 A 联网查询 (仅食材名)", "📋 B 解析原文 (已含数值)"],
            horizontal=True, key="ai_nutr_mode",
            help="A 用 gemini-2.5-flash + Google Search（25 次/天）；"
                 "B 用 gemini-flash-lite-latest 直接解析原文（高配额，更快更准）",
        )
        use_grounding = mode.startswith("🔍")

        if use_grounding:
            placeholder = "黄豆酱、冬瓜、芥兰苗"
        else:
            placeholder = (
                "冬瓜 每100g\n"
                "能量 11kcal · 蛋白质 0.4g · 脂肪 0.2g · 碳水 2.6g\n"
                "钠 1.8mg · 膳食纤维 0.7g · 钙 12mg · 钾 78mg · 维C 18mg ..."
            )
        raw = st.text_area(
            "输入",
            height=140,
            key="ai_nutr_raw",
            placeholder=placeholder,
            label_visibility="collapsed",
        )
        if st.button("🔍 AI 查询 / 解析", type="primary", use_container_width=True,
                     disabled=not raw.strip()):
            st.session_state.pop("ai_nutr_error", None)
            with st.spinner("Gemini 正在处理…（约 5-15 秒）"):
                try:
                    parsed = _ai_query_nutrition(raw.strip(), use_grounding=use_grounding)
                    if parsed:
                        st.session_state["ai_nutr_parsed"] = parsed
                    else:
                        st.session_state["ai_nutr_error"] = "未解析到任何结果，请检查输入"
                except Exception as e:
                    st.session_state["ai_nutr_error"] = f"查询失败：{e}"
            st.rerun()


def _tab_food_library() -> None:
    st.subheader("本地食材营养缓存库")
    _ai_nutrition_expander()
    st.divider()

    sc1, sc2 = st.columns([3, 1])
    sc1.caption("直接在表格中修改数值，点击「💾 保存修改」写入。来源标记将变为 manual。")
    if sc2.button("🔄 同步 local_nutrition.json", use_container_width=True,
                  help="将 seed_nutrition_ai.py 新写入的条目批量导入缓存，之后可在表格中编辑"):
        n = _sync_local_to_cache()
        if n:
            st.success(f"✅ 已同步 {n} 条新食材到缓存")
            st.rerun()
        else:
            st.info("local_nutrition.json 中无新条目（已全部同步）")

    conn = get_connection()
    try:
        db_cols = ", ".join(_COL_LABELS.keys())
        rows = conn.execute(
            f"SELECT {db_cols} FROM nutrition_cache ORDER BY ingredient_name"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        st.info("暂无缓存食材。在「🔍 食材查询」标签页查询后自动写入。")
        return

    orig_df = pd.DataFrame([dict(r) for r in rows])
    display_df = orig_df.rename(columns=_COL_LABELS)

    edited_df = st.data_editor(
        display_df,
        use_container_width=True,
        hide_index=True,
        disabled=["食材名", "来源"],
        num_rows="fixed",
        key="food_lib_editor",
    )

    rev_labels = {v: k for k, v in _COL_LABELS.items()}

    if st.button("💾 保存修改", type="primary"):
        edited_orig = edited_df.rename(columns=rev_labels)
        saved = 0
        for i in range(len(orig_df)):
            orig_row   = orig_df.iloc[i]
            edited_row = edited_orig.iloc[i]
            changed: dict = {}
            for db_col, key in _COL_TO_KEY.items():
                old = orig_row.get(db_col)
                new = edited_row.get(db_col)
                old_na = old is None or (isinstance(old, float) and pd.isna(old))
                new_na = new is None or (isinstance(new, float) and pd.isna(new))
                if old_na and new_na:
                    continue
                if new != old:
                    changed[key] = None if new_na else new
            if changed:
                update_cached_nutrients(str(orig_row["ingredient_name"]), changed)
                saved += 1
        if saved:
            st.success(f"已保存 {saved} 条修改")
            st.rerun()
        else:
            st.info("未检测到任何变更")

    st.divider()
    del_name = st.selectbox(
        "删除食材缓存（操作不可逆）",
        ["（选择食材）"] + list(orig_df["ingredient_name"]),
        label_visibility="visible",
    )
    if del_name != "（选择食材）" and st.button("🗑️ 删除此缓存", type="secondary"):
        invalidate_cache(del_name)
        st.success(f"「{del_name}」缓存已删除，下次查询将重新请求 USDA")
        st.rerun()


# ── Tab 4: 全日营养 (Phase 7) ─────────────────────────────────

_FRUITS_LIST = [
    "苹果", "香蕉", "橙子", "草莓", "葡萄", "蓝莓", "猕猴桃", "西瓜",
    "芒果", "菠萝", "桃子", "樱桃", "梨", "火龙果", "哈密瓜",
    "荔枝", "柚子", "覆盆子", "无花果","黑莓","香瓜"
]

# Per-person fixed nutrition — default breakfast (PRD estimate)
_BFST_BASE = dict(
    kcal=580, protein=33.5, fat=18, carbs=77,
    sodium=160, fiber=18.3, vitc=8, iron=4,
    calcium=350, potassium=1000, vitd=1.5, vita=60,
    magnesium=80, zinc=3,
)
_BFST_DETAIL = [
    "杂粮粥：干杂豆 35g · 钢切燕麦 25g · 三色藜麦 15g · 块茎 80g · 混合种子 15g · 燕麦麸皮 10g",
    "水煮鸡蛋（大）1 个 ~60g",
    "欧蕾：黑咖啡 240ml + 2% 超滤牛奶 150ml",
]

# Per-person fixed nutrition — default lunch (PRD estimate)
_LUNCH_BASE = dict(
    kcal=250, protein=18, fat=14, carbs=14,
    sodium=140, fiber=3.5, vitc=0, iron=0.5,
    calcium=400, potassium=500, vitd=2.0, vita=0,
    magnesium=30, zinc=1,
)
_LUNCH_DETAIL = [
    "高蛋白可可饮：2% 超滤牛奶 300ml + 未碱化可可粉 10g",
    "混合坚果 10g",
]

# Simplified ingredient name lists for default-meal diversity tracking
_BFST_DEFAULT_ING_NAMES = [
    "杂粮豆", "燕麦", "藜麦", "块茎蔬菜", "混合种子", "燕麦麸皮", "鸡蛋", "牛奶",
]
_LUNCH_DEFAULT_ING_NAMES = ["牛奶", "可可粉", "混合坚果"]

_NUTR_KEYS = [
    "kcal", "protein", "fat", "carbs", "sodium", "fiber",
    "vitc", "iron", "calcium", "potassium", "vitd", "vita", "magnesium", "zinc",
]


def _add_nutr(*sources) -> dict:
    """Sum nutrition dicts / MealNutrition objects into a plain dict."""
    total = {k: 0.0 for k in _NUTR_KEYS}
    for src in sources:
        for k in _NUTR_KEYS:
            v = src.get(k) if isinstance(src, dict) else getattr(src, k, 0.0)
            total[k] += float(v or 0)
    return total


def _meal_card(title: str, base: dict, detail_lines: list) -> None:
    with st.expander(title, expanded=False):
        for line in detail_lines:
            st.caption(f"• {line}")
    c = st.columns(4)
    c[0].metric("热量",   f"{base['kcal']:.0f} kcal")
    c[1].metric("蛋白质", f"{base['protein']:.1f} g")
    c[2].metric("脂肪",   f"{base['fat']:.1f} g")
    c[3].metric("碳水",   f"{base['carbs']:.1f} g")
    st.caption(f"钠 {base['sodium']:.0f} mg · 纤维 {base['fiber']:.1f} g")


# ── 备餐控制台 cross-page persistence ─────────────────────────
# These are widget keys, and Streamlit garbage-collects a widget's state on any
# run where that widget is not rendered. 今日规划 and 营养分析 both render them so
# hopping between those two is safe, but visiting ANY third page (库存, 菜谱库…)
# silently wipes them: the user's 临时加菜 / 主食 / 水果 vanish and the daily
# nutrition log is then saved without that food. Mirroring into plain (non-widget)
# keys makes the values survive; the widgets re-seed from the mirror.
_FD_KEYS = (
    "fd_bfst_skip", "fd_bfst_custom_txt", "fd_lunch_skip", "fd_lunch_custom_txt",
    "fd_staple_choice", "fd_staple_g", "fd_staple_custom_txt",
    "fd_fruits", "fd_fruit_g", "fd_dinner_addons_txt",
)


def restore_fd_state() -> None:
    """Re-seed 备餐控制台 keys from the mirror. Must run BEFORE those widgets render."""
    for k in _FD_KEYS:
        mirror = f"_keep_{k}"
        if k not in st.session_state and mirror in st.session_state:
            st.session_state[k] = st.session_state[mirror]


def remember_fd_state() -> None:
    """Mirror current 备餐控制台 values. Run AFTER those widgets render."""
    for k in _FD_KEYS:
        if k in st.session_state:
            st.session_state[f"_keep_{k}"] = st.session_state[k]


def compute_fullday_silent() -> dict:
    """Pure compute: reads session state, returns full-day nutrition dict.

    Callable from any page — e.g., plan.py uses it to JIT-compute PDF nutrition
    without forcing the user to visit the 营养分析 tab first.
    """
    restore_fd_state()   # the控制台 widgets may have been GC'd by a page switch
    zero = {k: 0.0 for k in _NUTR_KEYS}

    bfst_skip        = st.session_state.get("fd_bfst_skip", False)
    bfst_custom_txt  = st.session_state.get("fd_bfst_custom_txt", "").strip()
    lunch_skip       = st.session_state.get("fd_lunch_skip", False)
    lunch_custom_txt = st.session_state.get("fd_lunch_custom_txt", "").strip()
    staple_choice    = st.session_state.get("fd_staple_choice", "🍚 白米饭")
    staple_g         = int(st.session_state.get("fd_staple_g", 100))
    staple_custom_txt = st.session_state.get("fd_staple_custom_txt", "").strip()
    sel_fruits       = list(st.session_state.get("fd_fruits", []))
    fruit_g          = int(st.session_state.get("fd_fruit_g", 65))
    rids             = list(st.session_state.get("plan_rids", []))
    dinner_addons_txt= st.session_state.get("fd_dinner_addons_txt", "").strip()
    # 「✏️ 临时占位菜」from 今日规划. Same shape as _parse_placeholder output and
    # likewise cooked for two, so it joins dinner_ings and gets halved below.
    # Without this a placeholder-only dinner (乱炖 etc.) never reached daily_logs.
    plan_ph          = list(st.session_state.get("plan_ph", []))

    # ── Breakfast ─────────────────────────────────────────────
    bfst_custom_ings = []
    if bfst_skip:
        bfst_n, bfst_names = dict(zero), []
    elif bfst_custom_txt:
        bfst_custom_ings = _parse_placeholder(bfst_custom_txt)
        bn, _ = calc_nutrition_with_breakdown(bfst_custom_ings)
        bfst_n     = {k: getattr(bn, k, 0.0) for k in _NUTR_KEYS}
        bfst_names = [i["name"] for i in bfst_custom_ings]
    else:
        bfst_n, bfst_names = dict(_BFST_BASE), list(_BFST_DEFAULT_ING_NAMES)

    # ── Lunch ─────────────────────────────────────────────────
    lunch_custom_ings = []
    if lunch_skip:
        lunch_n, lunch_names = dict(zero), []
    elif lunch_custom_txt:
        lunch_custom_ings = _parse_placeholder(lunch_custom_txt)
        ln, _ = calc_nutrition_with_breakdown(lunch_custom_ings)
        lunch_n     = {k: getattr(ln, k, 0.0) for k in _NUTR_KEYS}
        lunch_names = [i["name"] for i in lunch_custom_ings]
    else:
        lunch_n, lunch_names = dict(_LUNCH_BASE), list(_LUNCH_DEFAULT_ING_NAMES)

    # ── Fruit ─────────────────────────────────────────────────
    fruit_n = dict(zero)
    if sel_fruits:
        fruit_ings = [{"name": f, "amount": float(fruit_g), "unit": "g", "intake_ratio": 1.0}
                      for f in sel_fruits]
        fn, _ = calc_nutrition_with_breakdown(fruit_ings)
        fruit_n = {k: getattr(fn, k, 0.0) for k in _NUTR_KEYS}

    # ── Dinner (÷2 per person, then ×serving_ratio) ──────────
    dinner_n, dinner_names = dict(zero), []
    if rids or dinner_addons_txt or plan_ph:
        dinner_ings = []
        if plan_ph:
            dinner_ings.extend(plan_ph)
            dinner_names.extend([i["name"] for i in plan_ph if i.get("name")])
        if rids:
            for rid in rids:
                r = get_recipe(rid)
                if r is None:
                    continue
                cond_r    = float(r.get("condiment_ratio") or 1.0)
                serving_r = float(r.get("serving_ratio")   or 1.0)
                for ing in get_ingredients(rid):
                    ratio = cond_r if ing.get("is_condiment") else 1.0
                    dinner_ings.append({
                        "name": ing["name"],
                        "amount": float(ing.get("amount") or 0) * serving_r,
                        "unit": ing.get("unit", "g"), "intake_ratio": ratio,
                    })
                    if not ing.get("is_condiment"):
                        dinner_names.append(ing["name"])

        if dinner_addons_txt:
            addons = _parse_placeholder(dinner_addons_txt)
            dinner_ings.extend(addons)
            dinner_names.extend([i["name"] for i in addons])

        if dinner_ings:
            dn, _ = calc_nutrition_with_breakdown(dinner_ings)
            dinner_n = {k: getattr(dn, k, 0.0) / 2.0 for k in _NUTR_KEYS}

    # ── Dinner staple (per person, not ÷2) ───────────────────
    staple_ings, staple_n, staple_names = [], dict(zero), []
    if staple_choice == "🍚 白米饭":
        staple_ings = [{"name": "米饭", "amount": float(staple_g), "unit": "g", "intake_ratio": 1.0}]
    elif staple_choice == "✏️ 自定义" and staple_custom_txt:
        staple_ings = _parse_placeholder(staple_custom_txt)
    if staple_ings:
        sn, _ = calc_nutrition_with_breakdown(staple_ings)
        staple_n     = {k: getattr(sn, k, 0.0) for k in _NUTR_KEYS}
        staple_names = [i["name"] for i in staple_ings]

    total    = _add_nutr(bfst_n, fruit_n, lunch_n, dinner_n, staple_n)
    snapshot = list(dict.fromkeys(bfst_names + sel_fruits + lunch_names + dinner_names + staple_names))

    return {
        "total": total, "bfst": bfst_n, "fruit": fruit_n,
        "lunch": lunch_n, "dinner": dinner_n, "staple": staple_n,
        "bfst_skip": bfst_skip, "lunch_skip": lunch_skip,
        "bfst_custom_ings": bfst_custom_ings, "lunch_custom_ings": lunch_custom_ings,
        "staple_ings": staple_ings, "snapshot": snapshot, "rids": rids,
    }


def get_pdf_nutrition_dict() -> dict:
    """JIT-build the nutr dict expected by `generate_daily_menu_pdf`.

    Pulls live session state via `compute_fullday_silent` — meaning whatever
    staple / fruit / dinner-addon settings the user has set (on either the
    plan page's control center or the 全日营养 tab) flow straight through.
    """
    fd = compute_fullday_silent()
    t  = fd["total"]
    return {
        "breakfast_kcal":    fd["bfst"]["kcal"] + fd["lunch"]["kcal"] + fd["fruit"]["kcal"],
        "breakfast_protein": fd["bfst"]["protein"] + fd["lunch"]["protein"] + fd["fruit"]["protein"],
        "lunch_kcal":        0,   # combined into breakfast row in PDF layout
        "lunch_protein":     0,
        "dinner_kcal":       fd["dinner"]["kcal"]   + fd["staple"]["kcal"],
        "dinner_protein":    fd["dinner"]["protein"] + fd["staple"]["protein"],
        "total_kcal":        t["kcal"],
        "total_protein":     t["protein"],
        "total_fat":         t["fat"],
        "total_carbs":       t["carbs"],
        "total_sodium":      t["sodium"],
        "total_fiber":       t["fiber"],
        "total_calcium":     t["calcium"],
        "total_iron":        t["iron"],
        "total_vitc":        t["vitc"],
        "total_potassium":   t["potassium"],
        "total_vitd":        t["vitd"],
        "total_vita":        t["vita"],
        "total_magnesium":   t["magnesium"],
        "total_zinc":        t["zinc"],
    }


def _do_compute_fullday() -> None:
    """UI wrapper: spinner + write to session state + rerun."""
    with st.spinner("计算全日营养…"):
        result = compute_fullday_silent()
    st.session_state["fd_result"] = result
    st.rerun()


def _tab_fullday() -> None:
    restore_fd_state()   # values may have been GC'd while the user was on another page
    st.caption("全日数据均为每人。早午餐可跳过或自定义，晚餐从今日规划导入（÷2 取每人份）。")

    # ── Breakfast ─────────────────────────────────────────────
    st.subheader("🌅 早餐")
    bfst_skip = st.toggle("今日不在家吃早饭", key="fd_bfst_skip")
    if not bfst_skip:
        _meal_card("食材明细（杂粮粥 + 鸡蛋 + 欧蕾）", _BFST_BASE, _BFST_DETAIL)
        with st.expander("✏️ 特殊情况：自定义食材（覆盖默认值）"):
            st.caption("每行 `食材名  数量  单位`，留空则使用默认估算值")
            st.text_area("早餐食材", height=90, key="fd_bfst_custom_txt",
                         label_visibility="collapsed",
                         placeholder="鸡蛋 60 g\n全麦面包 80 g\n花生酱 15 g")

    # ── Fruit picker ──────────────────────────────────────────
    st.subheader("🍎 今日水果")
    st.caption("每日 2–3 种，每种约 60–70g")
    c1, c2 = st.columns([4, 1])
    c1.multiselect("今日水果", _FRUITS_LIST, default=["苹果", "香蕉", "蓝莓"],
                   key="fd_fruits", label_visibility="collapsed")
    c2.number_input("每种(g)", min_value=30, max_value=200, value=65, step=5, key="fd_fruit_g")

    # ── Lunch ─────────────────────────────────────────────────
    st.subheader("🕛 午餐")
    lunch_skip = st.toggle("今日不在家吃午饭", key="fd_lunch_skip")
    if not lunch_skip:
        _meal_card("食材明细（高蛋白可可饮 + 坚果）", _LUNCH_BASE, _LUNCH_DETAIL)
        with st.expander("✏️ 特殊情况：自定义食材（覆盖默认值）"):
            st.caption("每行 `食材名  数量  单位`")
            st.text_area("午餐食材", height=80, key="fd_lunch_custom_txt",
                         label_visibility="collapsed",
                         placeholder="沙拉 200 g\n鸡胸肉 150 g\n橄榄油 10 ml")

    # ── Dinner ────────────────────────────────────────────────
    st.subheader("🌙 晚餐菜肴")
    rids = st.session_state.get("plan_rids", [])
    if rids:
        for rid in rids:
            r = get_recipe(rid)
            if not r:
                continue
            serving_pct = int(float(r.get("serving_ratio") or 1.0) * 100)
            st.caption(f"**{r['name']}** ·  个人份量 {serving_pct}%  ·  营养 ÷2 取每人份")
    else:
        st.info("今日规划为空，您也可以在此直接输入临时菜品进行计算。")
        
    with st.expander("➕ 临时加菜 (Add-ons)", expanded=False):
        st.caption("今晚临时往锅里加的配菜（如酸菜鱼里的莴笋片、打个鸡蛋等）。填写 **总克数**，计算时会自动同菜谱一起 ÷ 2 算作每人份。")
        st.text_area(
            "每行 `食材名  数量  单位`", 
            height=80, 
            key="fd_dinner_addons_txt",
            label_visibility="collapsed",
            placeholder="莴笋 150 g\n金针菇 100 g\n午餐肉 50 g"
        )

    # ── Dinner staple ─────────────────────────────────────────
    st.subheader("🍚 晚餐主食（每人）")
    st.radio("主食", ["🍚 白米饭", "🚫 不吃主食", "✏️ 自定义"],
             horizontal=True, key="fd_staple_choice", label_visibility="collapsed")
    staple_choice = st.session_state.get("fd_staple_choice", "🍚 白米饭")
    if staple_choice == "🍚 白米饭":
        st.number_input("克数（熟米饭，每人）", min_value=50, max_value=500,
                        value=100, step=10, key="fd_staple_g")
    elif staple_choice == "✏️ 自定义":
        st.text_area("主食食材（每人份）", height=80, key="fd_staple_custom_txt",
                     label_visibility="collapsed",
                     placeholder="意大利面（熟）150 g\n乌冬面（熟）200 g")

    remember_fd_state()   # all 备餐控制台 widgets rendered above — mirror them now

    st.divider()

    # ── Calculate ─────────────────────────────────────────────
    if st.button("📊 计算今日全日营养", type="primary", use_container_width=True):
        _do_compute_fullday()

    if "fd_result" not in st.session_state:
        return

    res      = st.session_state["fd_result"]
    total    = res["total"]
    bfst_n   = res["bfst"]
    fruit_n  = res["fruit"]
    lunch_n  = res["lunch"]
    dinner_n = res["dinner"]
    staple_n = res["staple"]

    # ── Total summary ─────────────────────────────────────────
    st.subheader("📊 全日合计（每人）")
    mc = st.columns(4)
    mc[0].metric("热量",   f"{total['kcal']:.0f} kcal")
    mc[1].metric("蛋白质", f"{total['protein']:.1f} g")
    mc[2].metric("脂肪",   f"{total['fat']:.1f} g")
    mc[3].metric("碳水",   f"{total['carbs']:.1f} g")

    # ── Save ──────────────────────────────────────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    if st.button("💾 保存今日记录", type="primary", use_container_width=True):
        save_daily_log(
            today, total,
            res["rids"],
            list(st.session_state.get("fd_fruits", [])),
            int(st.session_state.get("fd_fruit_g", 65)),
            bfst_skip=res["bfst_skip"],
            bfst_custom_ings=res.get("bfst_custom_ings") or [],
            lunch_skip=res["lunch_skip"],
            lunch_custom_ings=res.get("lunch_custom_ings") or [],
            staple_ings=res.get("staple_ings") or [],
            ingredients_snapshot=res.get("snapshot") or [],
        )
        st.success(f"✅ {today} 记录已保存")

    # ── Per-meal breakdown ────────────────────────────────────
    with st.expander("📋 各餐明细"):
        tbl_keys  = ["kcal", "protein", "fat", "carbs", "sodium", "fiber"]
        tbl_heads = ["热量(kcal)", "蛋白质(g)", "脂肪(g)", "碳水(g)", "钠(mg)", "纤维(g)"]
        rows = []
        for label, d in [
            ("🌅 早餐",    bfst_n),
            ("🍎 水果",    fruit_n),
            ("🕛 午餐",    lunch_n),
            ("🌙 晚餐/人", dinner_n),
            ("🍚 主食/人", staple_n),
            ("📊 合计",    total),
        ]:
            row = {"餐次": label}
            for k, h in zip(tbl_keys, tbl_heads):
                row[h] = round(float(d.get(k, 0.0)), 1)
            rows.append(row)
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # ── DRI bars ──────────────────────────────────────────────
    st.divider()
    st.subheader("全日 DRI 达成率（每人）")
    
    p_min, p_max = _protein_target()
    prot_dri_1p = p_min / 2.0
    
    for label, key, unit, warn in [
        ("热量",     "kcal",      "kcal", False),
        ("蛋白质",   "protein",   "g",    False),
        ("脂肪",     "fat",       "g",    False),
        ("碳水",     "carbs",     "g",    False),
        ("钠",       "sodium",    "mg",   True),
        ("膳食纤维", "fiber",     "g",    False),
        ("维生素C",  "vitc",      "mg",   False),
        ("铁",       "iron",      "mg",   False),
        ("钙",       "calcium",   "mg",   False),
        ("钾",       "potassium", "mg",   False),
        ("维生素D",  "vitd",      "µg",   False),
        ("维生素A",  "vita",      "µg",   False),
        ("镁",       "magnesium", "mg",   False),
        ("锌",       "zinc",      "mg",   False),
    ]:
        target = prot_dri_1p if key == "protein" else _DRI.get(key, 1.0)
        _bar(label, total.get(key, 0.0), target, unit, warn_over=warn)

    # ── AI Nutrition Advisor ───────────────────────────────────
    st.divider()
    try:
        from utils.nutrition_advisor import get_nutrition_advice, calls_remaining
        _advisor_ok = True
    except Exception:
        _advisor_ok = False

    if _advisor_ok:
        remaining = calls_remaining()
        with st.expander(f"🤖 听听 AI 怎么说（今日剩余 {remaining} 次）", expanded=False):
            if remaining <= 0:
                st.info("今日 AI 建议次数已用完，明天再来。")
            else:
                dinner_names = []
                rids_now = st.session_state.get("plan_rids", [])
                for rid in rids_now:
                    r = get_recipe(rid)
                    if r:
                        dinner_names.append(r["name"])
                if st.button("✨ 获取今日营养建议", type="primary", use_container_width=True,
                             key="ai_advice_btn"):
                    with st.spinner("AI 营养师正在分析…"):
                        advice = get_nutrition_advice(total, dinner_names, _DRI)
                    st.session_state["ai_advice"] = advice
                    st.rerun()
                if "ai_advice" in st.session_state:
                    st.markdown(st.session_state["ai_advice"])


_HISTORY_DRI = [
    ("kcal",      "热量",  2000, False),
    ("protein",   "蛋白质",  50, False),
    ("fat",       "脂肪",    65, False),
    ("carbs",     "碳水",   300, False),
    ("sodium",    "钠",    2300, True),   # warn_over → lower is better
    ("fiber",     "纤维",    25, False),
    ("vitc",      "维C",     90, False),
    ("iron",      "铁",      10, False),
    ("calcium",   "钙",    1000, False),
]


def _tab_history() -> None:
    logs = get_recent_logs_full(14)
    if not logs:
        st.info("暂无历史记录。在「🌅 全日营养」标签页计算并点击「💾 保存今日记录」。")
        return

    # ── Recent log table ──────────────────────────────────────
    st.caption(f"最近 {len(logs)} 天记录")
    tbl_keys  = ["date", "total_kcal", "total_protein", "total_fat", "total_carbs", "total_sodium", "total_fiber"]
    tbl_heads = ["日期", "热量(kcal)", "蛋白质(g)", "脂肪(g)", "碳水(g)", "钠(mg)", "纤维(g)"]
    rows = []
    for log in logs:
        row = {}
        for k, h in zip(tbl_keys, tbl_heads):
            v = log.get(k)
            row[h] = round(float(v), 1) if v is not None and k != "date" else (v or "")
        rows.append(row)
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if len(logs) >= 3:
        st.divider()
        kcals = [float(l.get("total_kcal") or 0) for l in reversed(logs)]
        prots = [float(l.get("total_protein") or 0) for l in reversed(logs)]
        st.caption("热量趋势（近期）")
        st.line_chart({"热量(kcal)": kcals, "蛋白质×10(g)": [p * 10 for p in prots]})

    # ── 7-day analysis ────────────────────────────────────────
    recent_7 = logs[:7]
    st.divider()
    st.subheader("🔬 近7日分析")

    # Ingredient diversity (only from logs that have snapshot)
    with_snap = [l for l in recent_7 if l.get("ingredients_snapshot")]
    if with_snap:
        all_names:   list = []
        all_fruits:  list = []
        daily_counts: list = []
        for log in with_snap:
            snap = json.loads(log["ingredients_snapshot"])
            all_names.extend(snap)
            all_fruits.extend(s for s in snap if s in _FRUITS_LIST)
            daily_counts.append(len(snap))

        unique_ings   = list(dict.fromkeys(all_names))
        unique_fruits = list(set(all_fruits))
        avg_per_day   = sum(daily_counts) / len(daily_counts)

        mc = st.columns(3)
        mc[0].metric("食材种类（合计）", f"{len(unique_ings)} 种")
        mc[1].metric("水果种类（合计）", f"{len(unique_fruits)} 种")
        mc[2].metric("每日平均食材",     f"{avg_per_day:.1f} 种")

        if unique_ings:
            with st.expander(f"📋 食材清单（{len(unique_ings)} 种）"):
                items = sorted(set(unique_ings))
                n = 5
                for chunk in [items[i:i+n] for i in range(0, len(items), n)]:
                    cols = st.columns(n)
                    for j, name in enumerate(chunk):
                        cols[j].caption(f"• {name}")
    else:
        st.caption("暂无食材快照数据（保存记录时自动生成）")

    # DRI completion heatmap
    st.subheader("📊 DRI 达成率（近7日）")
    
    p_min, p_max = _protein_target()
    prot_dri_1p = p_min / 2.0
    
    dri_rows = []
    for log in reversed(recent_7):
        if log.get("total_nutrients_json"):
            nutr = json.loads(log["total_nutrients_json"])
        else:
            nutr = {k: log.get(f"total_{k}", 0) for k in
                    ("kcal", "protein", "fat", "carbs", "sodium", "fiber")}

        row = {"日期": log["date"]}
        for key, label, default_dri, warn_over in _HISTORY_DRI:
            val = float(nutr.get(key) or 0)
            actual_dri = prot_dri_1p if key == "protein" else default_dri
            pct = val / actual_dri * 100 if actual_dri > 0 else 0.0
            
            icon = ("✅" if pct < 65 else "🟡" if pct < 100 else "🔴") if warn_over \
                else ("✅" if pct >= 80 else "🟡" if pct >= 50 else "🔴")
            row[label] = f"{icon}{pct:.0f}%"
        dri_rows.append(row)

    st.dataframe(dri_rows, use_container_width=True, hide_index=True)
    st.caption("✅ ≥80% DRI · 🟡 50–79% · 🔴 <50%　（钠反向：✅ <65% · 🟡 65–99% · 🔴 ≥100%）")


# ── Entry point ───────────────────────────────────────────────

def show() -> None:
    st.title("📊 营养分析")
    st.caption("四级降级查询：SQLite 缓存 → local_nutrition.json → USDA API → 未找到")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["🌅 全日营养", "🍽️ 菜谱营养计算", "🔍 食材查询", "🗄️ 食材库", "📈 历史记录"]
    )
    with tab1:
        _tab_fullday()
    with tab2:
        _tab_calc()
    with tab3:
        _tab_lookup()
    with tab4:
        _tab_food_library()
    with tab5:
        _tab_history()