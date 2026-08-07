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
from db.nutrition import (
    get_all_cached_names, get_cached, invalidate_cache, update_cached_nutrients,
)
from db.recipes import get_ingredients, get_recipe
from utils.cache import get_all_recipes_cached as get_all_recipes
from utils.nutrition_lookup import (
    MealNutrition, NutritionPer100g,
    lookup_ingredient, to_grams, calc_nutrition_with_breakdown,
)

# ── Daily Reference Intake — per person / day ─────────────────
# FDA Daily Values, which are all defined against a 2000 kcal reference diet.
_DRI_REFERENCE_KCAL = 2000.0

# Share of daily energy per macro, editable on the ⚙️ 设置 page. Defaults sit
# inside the AMDR bands (protein 10–35%, fat 20–35%, carbs 45–65%) and are
# weighted toward protein: at a calorie deficit, higher protein preserves lean
# mass. kcal/g: protein 4, fat 9, carbs 4.
_MACRO_DEFAULTS = {"protein": 25.0, "fat": 30.0, "carbs": 45.0}
_MACRO_KCAL_PER_G = {"protein": 4.0, "fat": 9.0, "carbs": 4.0}
_MACRO_KEYS = ("protein", "fat", "carbs")
# AHA guidance: saturated fat under 10% of daily energy (7% for heart health).
_SATFAT_ENERGY_LIMIT = 0.10


def get_macro_split() -> dict:
    """Macro energy shares (%) from user_settings, falling back to defaults."""
    try:
        conn = get_connection()
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM user_settings WHERE key LIKE 'macro_pct_%'"
        ).fetchall()}
        conn.close()
        split = {k: float(rows.get(f"macro_pct_{k}", _MACRO_DEFAULTS[k])) for k in _MACRO_KEYS}
        return split if abs(sum(split.values()) - 100.0) < 0.5 else dict(_MACRO_DEFAULTS)
    except Exception:
        return dict(_MACRO_DEFAULTS)


def save_macro_split(protein_pct: float, fat_pct: float, carbs_pct: float) -> None:
    conn = get_connection()
    try:
        for k, v in (("protein", protein_pct), ("fat", fat_pct), ("carbs", carbs_pct)):
            conn.execute("INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
                         (f"macro_pct_{k}", str(float(v))))
        conn.commit()
    finally:
        conn.close()

_DRI = {
    "kcal":       2000.0,   # kcal — generic default; per-user value comes from
                            # user_settings.target_kcal_per_day via _dri()
    "protein":      50.0,   # g   (overridden by user weight × multiplier)
    "fat":          65.0,   # g   = 29% of 2000 kcal — scaled with the target
    "carbs":       300.0,   # g   = 60% of 2000 kcal — scaled with the target
    "sodium":     2300.0,   # mg  (upper limit — not a target)
    "fiber":        25.0,   # g   (adult female recommendation, not energy-scaled)
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

_SUPPLEMENTS_KEY = "daily_supplements"


def get_supplements() -> dict:
    """Nutrients taken as a daily supplement, added to every day's total.

    Some nutrients are impractical to hit from food alone (vitamin D especially),
    so without this the DRI bars stay permanently red and stop being informative.
    Stored as {nutrient_key: amount_per_day} in the same units as the DRI table.
    """
    try:
        conn = get_connection()
        row = conn.execute("SELECT value FROM user_settings WHERE key=?",
                           (_SUPPLEMENTS_KEY,)).fetchone()
        conn.close()
        return json.loads(row["value"]) if row and row["value"] else {}
    except Exception:
        return {}


def save_supplements(data: dict) -> None:
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
                     (_SUPPLEMENTS_KEY, json.dumps(data, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def get_kcal_target() -> float:
    """Per-person daily calorie target from user_settings.

    `target_kcal_per_day` has existed in the seed data since the beginning but
    nothing ever read it — every DRI bar was scored against the hardcoded 2000,
    so the "% 达标" figure ignored the user's actual target entirely.
    """
    try:
        conn = get_connection()
        row = conn.execute("SELECT value FROM user_settings WHERE key=?",
                           ("target_kcal_per_day",)).fetchone()
        conn.close()
        return float(row["value"]) if row and row["value"] else _DRI["kcal"]
    except Exception:
        return _DRI["kcal"]


def _dri() -> dict:
    """DRI targets derived from the user's calorie goal and body weight.

    The three macros come from the configured energy split, so they always add
    up to exactly the calorie target. The FDA label values can't be used as-is:
    they assume 60/29/10 (carb/fat/protein), which both overshoots carbs and
    badly undershoots protein for anyone active.

    Vitamins, minerals and the sodium ceiling are absolute amounts and don't
    scale with how much you eat.
    """
    d = dict(_DRI)
    kcal = get_kcal_target()
    d["kcal"] = kcal
    for k, pct in get_macro_split().items():
        d[k] = kcal * (pct / 100.0) / _MACRO_KCAL_PER_G[k]
    d["satfat"] = kcal * _SATFAT_ENERGY_LIMIT / 9.0   # ceiling, not a goal
    return d


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


def recipe_ings_for_two(recipe_id: str, recipe: Optional[dict] = None) -> list:
    """A recipe's ingredients as *cooked-for-two* amounts, ready for calc_nutrition.

    The single source of truth for the gram pipeline. Callers still divide the
    resulting nutrition by 2 to get a per-person figure:

        每人摄入 = amount × serving_ratio × (condiment_ratio if 调料 else 1) ÷ 2

    Three knobs, each with a distinct job:
      serving_ratio   — per recipe: how much of the pot this meal actually eats
                        (a 4-serving 红烧肉 stretched over two dinners → 0.5)
      condiment_ratio — per recipe: how much of the seasoning is really ingested
                        (you don't drink all the braising soy sauce)
      ÷ 2             — two people share it

    Deliberately ignores `ingredients.intake_ratio`. That column predates
    `recipes.condiment_ratio` (migration step 1 vs step 4) and expresses the same
    idea per-ingredient; multiplying both discounts the seasoning twice. It is
    kept at 1.0 everywhere and no longer read.
    """
    if recipe is None:
        recipe = get_recipe(recipe_id) or {}
    cond_r    = float(recipe.get("condiment_ratio") or 1.0)
    serving_r = float(recipe.get("serving_ratio")   or 1.0)
    return [
        {
            "name":         ing["name"],
            "amount":       float(ing.get("amount") or 0) * serving_r,
            "unit":         ing.get("unit", "g"),
            "intake_ratio": cond_r if ing.get("is_condiment") else 1.0,
        }
        for ing in get_ingredients(recipe_id)
    ]


# Short alias used inside this module.
_recipe_ings = recipe_ings_for_two


# ── Display components ────────────────────────────────────────

def _bar(label: str, value: float, dri_1p: float, unit: str,
         warn_over: bool = False) -> None:
    """One nutrient progress bar — value and DRI both per-person."""
    if dri_1p > 0:
        pct      = value / dri_1p
        pct_disp = pct * 100
    else:
        pct = pct_disp = 0.0

    # Order matters: the >1.0 case must be tested first, otherwise >0.65 always
    # matches and 🚨 is unreachable — a hard overshoot looked identical to a mild one.
    if not warn_over:
        warn_icon = ""
    elif pct > 1.0:
        warn_icon = " 🚨"
    elif pct > 0.65:
        warn_icon = " ⚠️"
    else:
        warn_icon = ""

    bc, vc = st.columns([5, 2])
    bc.progress(min(pct, 1.0), text=f"{label}：**{value:.1f} {unit}**{warn_icon}")
    vc.caption(f"{pct_disp:.0f}% DRI/人")


# Editable nutrient fields: update_cached_nutrients() key → (DB column, label).
_FIX_FIELDS = [
    ("kcal", "kcal_per_100g", "热量 kcal"),      ("protein", "protein_per_100g", "蛋白质 g"),
    ("fat", "fat_per_100g", "脂肪 g"),           ("carbs", "carbs_per_100g", "碳水 g"),
    ("sodium", "sodium_per_100g", "钠 mg"),      ("fiber", "fiber_per_100g", "纤维 g"),
    ("vitc", "vitc_per_100g", "维C mg"),         ("iron", "iron_per_100g", "铁 mg"),
    ("calcium", "calcium_per_100g", "钙 mg"),    ("potassium", "potassium_per_100g", "钾 mg"),
    ("vitd", "vitd_per_100g", "维D µg"),         ("vita", "vita_per_100g", "维A µg"),
    ("magnesium", "magnesium_per_100g", "镁 mg"),("zinc", "zinc_per_100g", "锌 mg"),
    ("satfat", "satfat_per_100g", "饱和脂肪 g"),
    ("monofat", "monofat_per_100g", "单不饱和 g"),
    ("polyfat", "polyfat_per_100g", "多不饱和 g"),
]


def ingredient_fix_form(names: list, key_prefix: str) -> None:
    """Fix one ingredient's cached nutrition, right where you spotted the problem.

    The 食材营养缓存库 table is a virtualised data_editor: only the visible rows
    exist in the DOM, so Chrome's Ctrl+F can't find anything and hunting a row
    among ~660 is painful. This form takes the handful of ingredients actually on
    screen and edits one of them directly. Values are per 100g, matching the cache.
    """
    names = sorted({n for n in names if n and n not in ("—", "")})
    if not names:
        return

    with st.expander("✏️ 修正某个食材的营养数据（每 100g）", expanded=False):
        sel = st.selectbox("食材", names, key=f"{key_prefix}_sel")
        row = get_cached(sel)
        if not row:
            st.info(f"「{sel}」还不在缓存里——先在「🗄️ 食材营养库 → 🔍 食材查询」查询一次，"
                    "或用该页的「⚡ AI 录入营养数据」补上，之后就能在这里修正。")
            return

        st.caption(f"来源：`{row['source']}`　·　保存后来源标记变为 `manual`")
        en = st.text_input("英文名（可选）", value=row["en_name"] or "",
                           key=f"{key_prefix}_en")

        vals = {}
        cols = st.columns(4)
        for i, (key, db_col, label) in enumerate(_FIX_FIELDS):
            cur = row[db_col]
            vals[key] = cols[i % 4].number_input(
                label, value=float(cur) if cur is not None else 0.0,
                step=0.1, format="%.1f", key=f"{key_prefix}_{key}",
            )

        if st.button("💾 保存修正", type="primary", key=f"{key_prefix}_save"):
            changed = {}
            for key, db_col, _ in _FIX_FIELDS:
                cur, new = row[db_col], vals[key]
                if cur is None:
                    if new != 0.0:            # 0 on an empty field = still empty
                        changed[key] = new
                elif abs(float(cur) - new) > 1e-9:
                    changed[key] = new
            if (en or "").strip() != (row["en_name"] or ""):
                changed["en_name"] = en.strip() or None
            if not changed:
                st.info("没有检测到改动")
            else:
                update_cached_nutrients(sel, changed)
                st.success(f"✅ 已更新「{sel}」的 {len(changed)} 个字段，下次计算即生效")
                st.rerun()


def _render_fat_breakdown(fat: float, sat: float, mono: float, poly: float,
                          detailed: float, dri: dict, scope: str = "本餐") -> None:
    """Saturated / mono / poly split, stated with how much of the fat it covers.

    Most cached ingredients came from local_nutrition.json, which has no fat
    breakdown, so a bare "饱和 3g" would look like a complete figure when it may
    describe a small slice of the fat. The coverage % keeps that honest.
    All arguments are already per-person.
    """
    if fat <= 0:
        return
    coverage = detailed / fat if fat else 0.0
    if coverage <= 0:
        st.caption(f"🔬 脂肪细分：{scope}食材暂无饱和/不饱和数据"
                   "（可在「🗄️ 食材库」用 ⚡ AI 录入补齐）")
        return

    sat_limit = dri["kcal"] * _SATFAT_ENERGY_LIMIT / 9.0
    flag = "🚨" if sat > sat_limit else ("⚠️" if sat > sat_limit * 0.8 else "")
    st.caption(
        f"🔬 其中：饱和 **{sat:.1f} g**{flag} · 单不饱和 {mono:.1f} g · 多不饱和 {poly:.1f} g"
        f"　｜　饱和脂肪日上限约 {sat_limit:.0f} g"
    )
    if coverage < 0.95:
        st.caption(f"　　⚠️ 只有 {coverage*100:.0f}% 的脂肪有细分数据，实际饱和脂肪应高于上面的数字")


def _fat_breakdown(nutr: MealNutrition, pp: float, dri: dict) -> None:
    _render_fat_breakdown(nutr.fat / pp, nutr.satfat / pp, nutr.monofat / pp,
                          nutr.polyfat / pp, nutr.fat_detailed / pp, dri)


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
    dri = _dri()
    # All four bars share one source of truth, otherwise the protein bar (body
    # weight based) and the carb/fat bars (energy-share based) disagree about
    # what 100% means and can't all be satisfied at the calorie target.
    _bar("热量",   kcal_pp,  dri["kcal"],    "kcal")
    _bar("蛋白质", prot_pp,  dri["protein"], "g")
    _bar("脂肪",   fat_pp,   dri["fat"],     "g")
    _bar("碳水",   carbs_pp, dri["carbs"],   "g")
    _fat_breakdown(nutr, pp, dri)

    st.divider()

    # Micro bars
    st.subheader("Micros（每人 / 晚餐）")
    _bar("钠",     sodium_pp, dri["sodium"],    "mg", warn_over=True)
    if sodium_pp > _SODIUM_LIMIT_PER_PERSON:
        st.error(f"🚨 钠严重超标：每人 {sodium_pp:.0f}mg，超过全天上限 {_SODIUM_LIMIT_PER_PERSON:.0f}mg！建议减少调料用量或调整菜谱调料摄入比例。")
    elif sodium_pp > _SODIUM_WARN_PER_PERSON:
        st.warning(f"⚠️ 钠偏高：每人 {sodium_pp:.0f}mg，叠加早午餐可能超出全天上限。")

    _bar("膳食纤维", fiber_pp, dri["fiber"],     "g")
    _bar("维生素C",  vitc_pp,  dri["vitc"],      "mg")
    _bar("铁",       iron_pp,  dri["iron"],      "mg")
    _bar("钙",       cal_pp,   dri["calcium"],   "mg")
    _bar("钾",       pot_pp,   dri["potassium"], "mg")
    _bar("维生素D",  vitd_pp,  dri["vitd"],      "µg")
    _bar("维生素A",  vita_pp,  dri["vita"],      "µg")
    _bar("镁",       mag_pp,   dri["magnesium"], "mg")
    _bar("锌",       zinc_pp,  dri["zinc"],      "mg")

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
            ingredient_fix_form([r["食材"] for r in breakdown], "fixfd")


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
            rid = opts[name]
            ingredients.extend(_recipe_ings(rid))
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
            names.append(recipe["name"])
            ingredients.extend(_recipe_ings(rid, recipe))
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
    force_refresh = c2.checkbox(
        "强制刷新",
        help="忽略 SQLite 缓存重新查一次。注意：已在 local_nutrition.json 里人工"
             "校正过的食材仍以该文件为准（它就是用来盖掉 USDA 错误匹配的），"
             "要改这类食材请用下方「🗄️ 食材库」的编辑表格。",
    )
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
    "satfat_per_100g":    "satfat",
    "monofat_per_100g":   "monofat",
    "polyfat_per_100g":   "polyfat",
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
    "satfat_per_100g":    "饱和脂肪(g)",
    "monofat_per_100g":   "单不饱和(g)",
    "polyfat_per_100g":   "多不饱和(g)",
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
               "vitc", "iron", "calcium", "potassium", "vitd", "vita", "magnesium", "zinc",
               "satfat", "monofat", "polyfat"]
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
                "vitd", "vita", "magnesium", "zinc",
                "satfat", "monofat", "polyfat"]


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
  "satfat": 数值, "monofat": 数值, "polyfat": 数值,
  "source_note": "数据来源说明（如 USDA FDC 170069 / 中国食物成分表第6版 / 解析自原文）"
}}

satfat/monofat/polyfat = 饱和/单不饱和/多不饱和脂肪，三者之和应 ≈ fat；确实查不到就填 null，不要猜 0。
单位：protein/fat/carbs/fiber/satfat/monofat/polyfat=g, sodium/vitc/iron/calcium/potassium/magnesium/zinc=mg, vitd/vita=µg, kcal=kcal。
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


def _nutrition_gaps() -> tuple:
    """Recipe ingredients whose nutrition lookup yields nothing usable.

    Two distinct failure modes, both of which silently understate a meal:
      A. 未收录 — no row in nutrition_cache and no local_nutrition.json entry, so
         lookup_ingredient() returns None and the ingredient is dropped entirely.
      B. 空壳记录 — a cache row exists but kcal/protein/fat/carbs are all 0/NULL,
         so it contributes exactly nothing while *looking* like it resolved.
         Sodium is checked too: 盐/小苏打 legitimately have zero macros but real
         sodium, and flagging those as broken would be noise.

    Each is returned with the number of recipes affected, so the list can be
    worked through by actual impact instead of alphabetically.
    """
    conn = get_connection()
    try:
        used = conn.execute(
            "SELECT name, is_condiment, COUNT(DISTINCT recipe_id) AS n "
            "FROM ingredients GROUP BY name, is_condiment"
        ).fetchall()
        cached = {r["ingredient_name"] for r in
                  conn.execute("SELECT ingredient_name FROM nutrition_cache")}
        hollow = {r["ingredient_name"] for r in conn.execute(
            "SELECT ingredient_name FROM nutrition_cache "
            "WHERE COALESCE(kcal_per_100g,0)=0 AND COALESCE(protein_per_100g,0)=0 "
            "  AND COALESCE(fat_per_100g,0)=0 AND COALESCE(carbs_per_100g,0)=0 "
            "  AND COALESCE(sodium_per_100g,0)=0"
        )}
    finally:
        conn.close()

    from utils.nutrition_lookup import _load_local
    # `_` keys are comments in that JSON, not ingredients.
    local = {k for k in _load_local() if not k.startswith("_")}
    have = cached | local

    absent, empty = [], []
    for r in used:
        item = {"name": r["name"], "recipes": r["n"], "condiment": bool(r["is_condiment"])}
        if r["name"] not in have:
            absent.append(item)
        elif r["name"] in hollow:
            # Flagged regardless of local_nutrition.json: the SQLite cache is
            # tier 1 and shadows it, so a hollow cache row wins even when the
            # JSON holds good values (the 牛排 bug). Excluding "it's in local
            # too" would hide exactly that case.
            empty.append(item)

    key = lambda x: (x["condiment"], -x["recipes"], x["name"])
    return sorted(absent, key=key), sorted(empty, key=key)


# Zero calories here is correct, not a data gap. Exact names only — a suffix rule
# like "ends with 水" would also swallow 糖水/汽水, where a 0 really is an error.
_ZERO_IS_FINE = {
    "水", "清水", "冷水", "开水", "热水", "温水", "冰水", "凉水", "纯净水",
    "饮用水", "泡发用水", "葱姜水", "泡椒水", "适量清水", "适量水", "冰块",
}
# Recipe-import artefacts that aren't ingredients at all (CLAUDE.md 已知问题 1).
_JUNK_PREFIXES = ("步骤", "详见", "比例", "见步骤", "以上", "备注")


def _is_noise(name: str) -> bool:
    return name in _ZERO_IS_FINE or name.startswith(_JUNK_PREFIXES)


def _gap_section(title: str, items: list, note: str) -> None:
    if not items:
        st.success(f"✅ {title}：没有问题")
        return
    mains = [i for i in items if not i["condiment"]]
    conds = [i for i in items if i["condiment"]]
    st.markdown(f"**{title}**　共 {len(items)} 种（主料 {len(mains)} · 调料 {len(conds)}）")
    st.caption(note)
    for group, label in ((mains, "主料"), (conds, "调料")):
        if not group:
            continue
        st.caption(f"— {label} —")
        st.dataframe(
            [{"食材": i["name"], "被几道菜使用": i["recipes"]} for i in group],
            use_container_width=True, hide_index=True,
        )
    st.caption("复制下面这行，粘到上方「⚡ AI 录入营养数据」即可批量补齐：")
    st.code("、".join(i["name"] for i in mains) or "（无主料需补）", language=None)


def _tab_food_library() -> None:
    st.subheader("本地食材营养缓存库")
    _ai_nutrition_expander()

    with st.expander("🩺 营养数据体检：哪些食材没有营养数据", expanded=False):
        st.caption(
            "菜谱里用到、但查不到营养数据的食材会被**静默跳过**——菜看着算出来了，"
            "实际热量和蛋白质都偏低。按「被几道菜使用」排序，先补影响大的。"
        )
        absent, empty = _nutrition_gaps()
        hide = st.checkbox(
            "隐藏水类和非食材条目", value=True, key="gap_hide_noise",
            help="水/开水这类零卡是正确的；「步骤1」「详见步骤」是菜谱导入时混进来的文本，"
                 "不是真食材。取消勾选可看到完整列表。",
        )
        if hide:
            n_before = len(absent) + len(empty)
            absent = [i for i in absent if not _is_noise(i["name"])]
            empty  = [i for i in empty  if not _is_noise(i["name"])]
            n_hidden = n_before - len(absent) - len(empty)
            if n_hidden:
                st.caption(f"已隐藏 {n_hidden} 条水类/非食材条目")

        _gap_section(
            "① 完全未收录", absent,
            "缓存和 local_nutrition.json 里都没有 → 计算时整个食材被忽略。",
        )
        st.divider()
        _gap_section(
            "② 有记录但是空壳", empty,
            "缓存里有这一条，但热量/蛋白/脂肪/碳水全是 0（且钠也为 0）→ 看起来匹配上了，"
            "实际按 0 计入。盐、小苏打这类本身零卡但含钠的不会出现在这里。",
        )

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

    # The data_editor below is virtualised — only visible rows are in the DOM, so
    # Chrome's Ctrl+F finds nothing. This filter is the way to locate a row.
    q = st.text_input("🔍 按食材名筛选", key="food_lib_filter",
                      placeholder="输入食材名的一部分，如「牛」「番茄」")
    if q.strip():
        orig_df = orig_df[orig_df["ingredient_name"].str.contains(q.strip(), na=False)]
        st.caption(f"匹配 **{len(orig_df)}** 条")
        if orig_df.empty:
            st.info("没有匹配的食材")
            return
        orig_df = orig_df.reset_index(drop=True)
    else:
        st.caption(f"共 **{len(orig_df)}** 条（表格无法用浏览器搜索，请用上方筛选框定位）")

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

_FRUITS_BASE = [
    "苹果", "香蕉", "橙子", "草莓", "葡萄", "蓝莓", "猕猴桃", "西瓜",
    "芒果", "菠萝", "桃子", "樱桃", "梨", "火龙果", "哈密瓜",
    "荔枝", "柚子", "黑莓", "香瓜",
]
_CUSTOM_FRUITS_KEY = "custom_fruits"


def _load_custom_fruits() -> list:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM user_settings WHERE key=?",
                           (_CUSTOM_FRUITS_KEY,)).fetchone()
    finally:
        conn.close()
    if not row:
        return []
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return []


def get_fruits_list() -> list:
    """Built-in fruits plus any the user typed in, so additions survive restarts.
    `accept_new_options` alone would only keep a new fruit for that one session."""
    return _FRUITS_BASE + [f for f in _load_custom_fruits() if f not in _FRUITS_BASE]


def remember_new_fruits(selected: list) -> None:
    """Persist fruits typed straight into the multiselect."""
    known = set(get_fruits_list())
    new = [f for f in (selected or []) if f and f not in known]
    if not new:
        return
    custom = _load_custom_fruits() + new
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
                     (_CUSTOM_FRUITS_KEY, json.dumps(custom, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

# Per-person fixed nutrition — default breakfast (PRD estimate)
# Default breakfast/lunch as real ingredient lists rather than a hardcoded
# nutrient dict. The old dict was a rough PRD-era estimate and its micronutrients
# were badly wrong — vitA 60µg against an actual ~640µg (南瓜+红薯 are dense
# β-carotene sources) and magnesium 80mg against ~270mg (火麻仁 alone is
# 700mg/100g). That single dict was the main reason 维A/镁 looked permanently
# deficient on the DRI heatmap. Going through lookup_ingredient() instead means
# these track the ingredient database as it improves, and show up in 食材明细.
_BFST_INGS = [
    {"name": "干杂豆",   "amount": 35,  "unit": "g", "intake_ratio": 1.0},
    {"name": "钢切燕麦", "amount": 25,  "unit": "g", "intake_ratio": 1.0},
    {"name": "三色藜麦", "amount": 15,  "unit": "g", "intake_ratio": 1.0},
    {"name": "南瓜",     "amount": 40,  "unit": "g", "intake_ratio": 1.0},
    {"name": "红薯",     "amount": 40,  "unit": "g", "intake_ratio": 1.0},
    {"name": "奇亚籽",   "amount": 7.5, "unit": "g", "intake_ratio": 1.0},
    {"name": "火麻仁",   "amount": 7.5, "unit": "g", "intake_ratio": 1.0},
    {"name": "燕麦麸皮", "amount": 10,  "unit": "g", "intake_ratio": 1.0},
    {"name": "鸡蛋",     "amount": 60,  "unit": "g", "intake_ratio": 1.0},
    {"name": "牛奶",     "amount": 150, "unit": "g", "intake_ratio": 1.0},
    {"name": "黑咖啡",   "amount": 240, "unit": "g", "intake_ratio": 1.0},
]
_BFST_DETAIL = [
    "杂粮粥：干杂豆 35g · 钢切燕麦 25g · 三色藜麦 15g · 南瓜 40g · 红薯 40g · 奇亚籽 7.5g · 火麻仁 7.5g · 燕麦麸皮 10g",
    "水煮鸡蛋（大）1 个 ~60g",
    "欧蕾：黑咖啡 240ml + 2% 超滤牛奶 150ml",
]

_LUNCH_INGS = [
    {"name": "牛奶",       "amount": 300, "unit": "g", "intake_ratio": 1.0},
    {"name": "可可粉",     "amount": 10,  "unit": "g", "intake_ratio": 1.0},
    {"name": "混合坚果",   "amount": 10,  "unit": "g", "intake_ratio": 1.0},
]
_LUNCH_DETAIL = [
    "高蛋白可可饮：2% 超滤牛奶 300ml + 未碱化可可粉 10g",
    "混合坚果 10g",
]

_NUTR_KEYS = [
    "kcal", "protein", "fat", "carbs", "sodium", "fiber",
    "vitc", "iron", "calcium", "potassium", "vitd", "vita", "magnesium", "zinc",
    # Fat breakdown. `fat_detailed` is not a nutrient — it's how many grams of
    # the total fat came from ingredients that actually have a breakdown, so the
    # UI can say how complete the saturated-fat figure is instead of implying
    # it covers the whole meal.
    "satfat", "monofat", "polyfat", "fat_detailed",
]


def _add_nutr(*sources) -> dict:
    """Sum nutrition dicts / MealNutrition objects into a plain dict."""
    total = {k: 0.0 for k in _NUTR_KEYS}
    for src in sources:
        for k in _NUTR_KEYS:
            v = src.get(k) if isinstance(src, dict) else getattr(src, k, 0.0)
            total[k] += float(v or 0)
    return total


def _meal_card(title: str, ings: list, detail_lines: list) -> None:
    """Default-meal summary, computed from the ingredient list like every other
    meal — no separate hardcoded numbers that can drift from the real data."""
    n, _ = calc_nutrition_with_breakdown(ings)
    with st.expander(title, expanded=False):
        for line in detail_lines:
            st.caption(f"• {line}")
        if n.missing:
            st.warning(f"⚠️ 未查到营养数据（按 0 计）：{'、'.join(n.missing)}")
    c = st.columns(4)
    c[0].metric("热量",   f"{n.kcal:.0f} kcal")
    c[1].metric("蛋白质", f"{n.protein:.1f} g")
    c[2].metric("脂肪",   f"{n.fat:.1f} g")
    c[3].metric("碳水",   f"{n.carbs:.1f} g")
    st.caption(f"钠 {n.sodium:.0f} mg · 纤维 {n.fiber:.1f} g · "
               f"镁 {n.magnesium:.0f} mg · 维A {n.vita:.0f} µg")


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
        bn, _ = calc_nutrition_with_breakdown(_BFST_INGS)
        bfst_n     = {k: getattr(bn, k, 0.0) for k in _NUTR_KEYS}
        bfst_names = [i["name"] for i in _BFST_INGS]

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
        ln, _ = calc_nutrition_with_breakdown(_LUNCH_INGS)
        lunch_n     = {k: getattr(ln, k, 0.0) for k in _NUTR_KEYS}
        lunch_names = [i["name"] for i in _LUNCH_INGS]

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
                dinner_ings.extend(_recipe_ings(rid, r))
                dinner_names.extend(
                    ing["name"] for ing in get_ingredients(rid)
                    if not ing.get("is_condiment")
                )

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
    # Daily supplements land on top of everything eaten (see get_supplements).
    supp = get_supplements()
    for k, v in supp.items():
        if k in total:
            total[k] = total[k] + float(v or 0)
    snapshot = list(dict.fromkeys(bfst_names + sel_fruits + lunch_names + dinner_names + staple_names))

    return {
        "total": total, "supplements": supp, "bfst": bfst_n, "fruit": fruit_n,
        "lunch": lunch_n, "dinner": dinner_n, "staple": staple_n,
        "bfst_skip": bfst_skip, "lunch_skip": lunch_skip,
        "bfst_custom_ings": bfst_custom_ings, "lunch_custom_ings": lunch_custom_ings,
        "staple_ings": staple_ings, "snapshot": snapshot, "rids": rids,
        # Carried in the result so save_daily_log() records the fruit that this
        # total was actually computed from. Reading the widgets again at save
        # time let a fruit changed after 计算 land in the log while its calories
        # did not — the stored total and the stored fruit list disagreed.
        "fruits": sel_fruits, "fruit_g": fruit_g,
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
        _meal_card("食材明细（杂粮粥 + 鸡蛋 + 欧蕾）", _BFST_INGS, _BFST_DETAIL)
        with st.expander("✏️ 特殊情况：自定义食材（覆盖默认值）"):
            st.caption("每行 `食材名  数量  单位`，留空则使用默认估算值")
            st.text_area("早餐食材", height=90, key="fd_bfst_custom_txt",
                         label_visibility="collapsed",
                         placeholder="鸡蛋 60 g\n全麦面包 80 g\n花生酱 15 g")

    # ── Fruit picker ──────────────────────────────────────────
    st.subheader("🍎 今日水果")
    st.caption("每日 2–3 种，每种约 60–70g")
    c1, c2 = st.columns([4, 1])
    c1.multiselect("今日水果", get_fruits_list(), default=["苹果", "香蕉", "蓝莓"],
                   key="fd_fruits", label_visibility="collapsed",
                   accept_new_options=True,
                   placeholder="选择或直接输入新水果（会被记住）")
    c2.number_input("每种(g)", min_value=30, max_value=200, value=65, step=5, key="fd_fruit_g")
    remember_new_fruits(st.session_state.get("fd_fruits"))

    # ── Lunch ─────────────────────────────────────────────────
    st.subheader("🕛 午餐")
    lunch_skip = st.toggle("今日不在家吃午饭", key="fd_lunch_skip")
    if not lunch_skip:
        _meal_card("食材明细（高蛋白可可饮 + 坚果）", _LUNCH_INGS, _LUNCH_DETAIL)
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
            list(res.get("fruits") or []),
            int(res.get("fruit_g") or 65),
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
    
    prot_dri_1p = _dri()["protein"]   # same target as the 全日营养 bars

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
        target = prot_dri_1p if key == "protein" else _dri().get(key, 1.0)
        _bar(label, total.get(key, 0.0), target, unit, warn_over=warn)
        if key == "fat":
            # total[] is already per-person here, so no further division.
            _render_fat_breakdown(
                total.get("fat", 0.0), total.get("satfat", 0.0),
                total.get("monofat", 0.0), total.get("polyfat", 0.0),
                total.get("fat_detailed", 0.0), _dri(), scope="今日",
            )

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
                        advice = get_nutrition_advice(total, dinner_names, _dri())
                    st.session_state["ai_advice"] = advice
                    st.rerun()
                if "ai_advice" in st.session_state:
                    st.markdown(st.session_state["ai_advice"])


# Same 14 nutrients the 全日营养 bars show, so the two views can't disagree —
# daily_logs already stored all of them, the heatmap just wasn't displaying
# 钾/维D/维A/镁/锌. The numeric column is only a fallback: live targets come
# from _dri(), which follows the user's calorie goal and macro split.
# 饱和脂肪 is warn_over (a ceiling, like sodium) rather than a goal to hit.
_HISTORY_DRI = [
    ("kcal",      "热量",   2000, False),
    ("protein",   "蛋白质",   50, False),
    ("fat",       "脂肪",     65, False),
    ("satfat",    "饱和脂肪", 22, True),    # ceiling ≈10% of energy
    ("carbs",     "碳水",    300, False),
    ("sodium",    "钠",     2300, True),   # warn_over → lower is better
    ("fiber",     "纤维",     25, False),
    ("vitc",      "维C",      90, False),
    ("iron",      "铁",       10, False),
    ("calcium",   "钙",     1000, False),
    ("potassium", "钾",     4700, False),
    ("vitd",      "维D",      15, False),
    ("vita",      "维A",     800, False),
    ("magnesium", "镁",      350, False),
    ("zinc",      "锌",       10, False),
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
        known_fruits = set(get_fruits_list())   # includes user-added ones
        for log in with_snap:
            snap = json.loads(log["ingredients_snapshot"])
            all_names.extend(snap)
            all_fruits.extend(s for s in snap if s in known_fruits)
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
    
    prot_dri_1p = _dri()["protein"]   # same target as the 全日营养 bars

    dri_rows = []
    for log in reversed(recent_7):
        if log.get("total_nutrients_json"):
            nutr = json.loads(log["total_nutrients_json"])
        else:
            nutr = {k: log.get(f"total_{k}", 0) for k in
                    ("kcal", "protein", "fat", "carbs", "sodium", "fiber")}

        row = {"日期": log["date"]}
        dri_now = _dri()   # kcal from settings; carbs/fat scaled to it
        for key, label, default_dri, warn_over in _HISTORY_DRI:
            # A key absent from the stored snapshot means "not tracked back then",
            # not "zero". Showing 0% would paint 饱和脂肪 green (✅ under the
            # ceiling) for every log saved before that field existed.
            if key not in nutr:
                row[label] = "—"
                continue
            val = float(nutr.get(key) or 0)
            # protein is body-weight based; everything else follows _dri() so the
            # heatmap can't disagree with the bars on the 全日营养 tab.
            actual_dri = prot_dri_1p if key == "protein" else dri_now.get(key, default_dri)
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