"""菜谱库 UI"""
import json
import os
import re
from typing import Optional

import streamlit as st

from db.recipes import get_recipe, get_ingredients
# Write functions come from utils.cache, whose wrappers invalidate the read cache
# automatically — importing them from db.recipes would skip that.
from utils.cache import (
    get_all_recipes_cached as get_all_recipes, invalidate_recipes_cache,
    create_recipe, update_recipe, mark_cooked, delete_recipe,
)
try:
    from utils.semantic_search import (
        semantic_search, index_recipe, index_all_recipes, get_indexed_count,
        delete_recipe as _sem_delete,
    )
    _SEMANTIC_OK = True
except Exception:
    _SEMANTIC_OK = False

# ── Constants ─────────────────────────────────────────────────

CATEGORIES_MEAT = ["纯蛋白", "半蛋白半素", "纯素"]
CATEGORIES_FORM = ["菜肴", "主食", "甜点", "早餐", "饮料", "冷冻", "预制"]
DINNER_CATEGORIES = {"菜肴", "主食"}

METHODS = ["炒", "蒸", "烤", "煮", "汤", "凉拌", "炸", "炖", "煎"]
DIFFICULTIES = ["简单", "中等", "繁琐"]
UNITS = ["g", "ml", "个", "片", "根", "条", "块", "把", "汤匙", "茶匙", "斤", "两"]

CUISINES = ["", "家常", "川菜", "粤菜", "湘菜", "东北菜", "淮扬菜", "西北菜",
            "日式", "韩式", "泰式", "越式", "意式", "法式", "美式", "墨西哥",
            "地中海", "印度", "中东", "其他"]

QUALITY_LABEL = {
    "complete":     "✅ 完整",
    "needs_review": "⚠️ 待补全",
    "estimated":    "~ 估算",
}

_CAT_COLORS = {
    "纯蛋白": "red", "半蛋白半素": "orange", "纯素": "green",
    "汤": "violet", "凉拌": "gray", "主食": "orange",
}


# ── Helpers ───────────────────────────────────────────────────

def _cat_badges(cats: list) -> str:
    parts = []
    for c in cats:
        color = _CAT_COLORS.get(c, "gray")
        parts.append(f":{color}[{c}]")
    return "  ".join(parts)


def _method_tags(methods: list) -> str:
    return " · ".join(f"`{m}`" for m in methods)


# ── Navigation ────────────────────────────────────────────────

def _clear_form_state() -> None:
    """Remove all form-related session_state keys."""
    for k in list(st.session_state.keys()):
        if k.startswith("rf_") or k.startswith("ing_") or k in (
            "draft_ing_ids", "ing_counter",
        ):
            del st.session_state[k]


def _goto_list() -> None:
    _clear_form_state()
    for k in ("rid", "rmode", "confirm_delete"):
        st.session_state.pop(k, None)
    st.session_state["rv"] = "list"


def _goto_detail(rid: str) -> None:
    st.session_state.pop("confirm_delete", None)
    st.session_state["rv"] = "detail"
    st.session_state["rid"] = rid


def _goto_ai_onboard() -> None:
    _clear_form_state()
    for k in ("ai_ob_raw", "ai_ob_recipe", "ai_ob_ings", "ai_ob_rid"):
        st.session_state.pop(k, None)
    st.session_state["rv"] = "ai_onboard"


def _goto_form(mode: str, rid: Optional[str] = None) -> None:
    _clear_form_state()
    st.session_state["rv"] = "form"
    st.session_state["rmode"] = mode
    if rid:
        st.session_state["rid"] = rid
    if mode == "edit" and rid:
        _prefill_form(rid)
    else:
        _blank_form()


def _blank_form() -> None:
    st.session_state.update({
        "rf_name": "",
        "rf_url": "",
        "rf_methods": [],
        "rf_wok": False,
        "rf_parallel": False,
        "rf_difficulty": "中等",
        "rf_active_time": 20,
        "rf_idle_time": 0,
        "rf_cat_meat": [],
        "rf_cat_form": "",
        "rf_tags": "",
        "rf_quality": "needs_review",
        "rf_notes": "",
        "rf_en_name": "",
        "rf_en_desc": "",
        "rf_zh_desc": "",
        "rf_condiment_ratio": 100,
        "rf_serving_ratio": 100,
        "rf_cuisine": "家常",
        "rf_pairing_ids": [],
        "rf_steps": "",
        "draft_ing_ids": [],
        "ing_counter": 0,
    })


def _prefill_form(rid: str) -> None:
    recipe = get_recipe(rid)
    ings   = get_ingredients(rid)
    if not recipe:
        _blank_form()
        return

    cat = recipe.get("category", [])
    st.session_state.update({
        "rf_name":       recipe["name"],
        "rf_url":        recipe.get("source_url") or "",
        "rf_methods":    recipe.get("cooking_method", []),
        "rf_wok":        bool(recipe.get("uses_wok")),
        "rf_parallel":   bool(recipe.get("is_parallel")),
        "rf_difficulty": recipe.get("prep_difficulty", "中等"),
        "rf_active_time": int(recipe.get("active_time_min") or recipe.get("cook_time_min") or 20),
        "rf_idle_time":   int(recipe.get("idle_time_min")   or 0),
        "rf_cat_meat":   [c for c in cat if c in CATEGORIES_MEAT],
        "rf_cat_form":   next((c for c in cat if c in CATEGORIES_FORM), ""),
        "rf_tags":            ", ".join(recipe.get("tags", [])),
        "rf_quality":         recipe.get("data_quality", "needs_review"),
        "rf_notes":           recipe.get("notes") or "",
        "rf_en_name":         recipe.get("en_name") or "",
        "rf_en_desc":         recipe.get("en_desc") or "",
        "rf_zh_desc":         recipe.get("zh_desc") or "",
        "rf_condiment_ratio": int(float(recipe.get("condiment_ratio", 1.0)) * 100),
        "rf_serving_ratio":   int(float(recipe.get("serving_ratio",   1.0)) * 100),
        "rf_cuisine":         recipe.get("cuisine") or "",
        "rf_pairing_ids":     list(recipe.get("pairing_ids") or []),
        "rf_steps":           "\n".join(recipe.get("steps") or []),
    })

    ids = []
    for i, ing in enumerate(ings):
        sid = f"i{i}"
        ids.append(sid)
        unit_val = ing.get("unit", "g")
        if unit_val not in UNITS:
            unit_val = "g"
        st.session_state[f"ing_{sid}_name"]  = ing["name"]
        st.session_state[f"ing_{sid}_amt"]   = float(ing.get("amount") or 0)
        st.session_state[f"ing_{sid}_unit"]  = unit_val
        st.session_state[f"ing_{sid}_cond"]  = bool(ing.get("is_condiment"))

    st.session_state["draft_ing_ids"] = ids
    st.session_state["ing_counter"]   = len(ings)


# ── Ingredient slot ───────────────────────────────────────────

def _ingredient_slot(sid: str) -> None:
    """Render one dynamic ingredient row (name | amount | unit | condiment | delete)."""
    c1, c2, c3, c4, c5 = st.columns([3, 1.5, 1.8, 1.5, 0.7])

    c1.text_input(
        "食材名",
        key=f"ing_{sid}_name",
        placeholder="食材名称",
        label_visibility="collapsed",
    )
    c2.number_input(
        "用量",
        min_value=0.0,
        step=10.0,
        format="%.0f",
        key=f"ing_{sid}_amt",
        label_visibility="collapsed",
    )
    c3.selectbox(
        "单位",
        UNITS,
        key=f"ing_{sid}_unit",
        label_visibility="collapsed",
    )
    c4.checkbox("调料", key=f"ing_{sid}_cond")

    if c5.button("✕", key=f"del_ing_{sid}"):
        st.session_state["draft_ing_ids"].remove(sid)
        for k in list(st.session_state.keys()):
            if k.startswith(f"ing_{sid}_"):
                del st.session_state[k]
        st.rerun()


# ── List view ─────────────────────────────────────────────────

# ── AI onboarding helpers ─────────────────────────────────────

_AI_PARSE_PROMPT = """你是一位拥有 20 年经验的资深大厨和数据专家，同时精通中英双语食谱写作。
请对以下非结构化菜谱文字进行深度结构化处理。

【菜谱原文】：
{raw}

━━━━━━━━━━━━━━━━━━━━━━━━━━
【任务 1：密度感知的精确换算】
将所有非 g/ml 单位换算为克数写入 amount_g：
- 水/油/奶/生抽/醋/料酒/盐：1 tsp=5.0g, 1 tbsp=15.0g
- 泡打粉/酵母/胡椒/五香粉：1 tsp=3.0g, 1 tbsp=9.0g
- 淀粉/面粉：1 tsp=4.0g, 1 tbsp=12.0g
- 糖/蜂蜜：1 tsp=4.5g, 1 tbsp=14.0g
- 1 lb=453.6g, 1 cup=240.0g, 1 oz=28.3g
- 葱姜蒜等未写克数的请估算：葱10g 姜5g 蒜10g 小米辣5g

【任务 2：分类与预估】
- category：第一元素荤素维度（"荤"/"素"/"荤素"，可省略）；第二元素形态维度（"菜肴"/"主食"/"甜点"/"早餐"/"饮料"/"冷冻"/"预制"，必填）
- cooking_method：["炒"|"蒸"|"烤"|"煮"|"汤"|"凉拌"|"炸"|"炖"|"煎"] 一或多个
- condiment_ratio：调料实际摄入比（炒菜≈0.5，汤≈0.3，凉拌≈0.9）
- active_time_min：需要持续操作的时间（切菜+翻炒+调味），整数分钟
- idle_time_min：无需看守的等待时间（腌制/炖煮/蒸/发酵），整数分钟；纯快炒填0
- cuisine：菜系（"家常"/"川菜"/"粤菜"/"日式"/"韩式"/"泰式"/"意式"/"美式"等），无明显风格填"家常"

【任务 3：步骤重写】
格式要求：每步描述动作，调料需在括号内内嵌名称和克重。
示例格式：「鸡蛋（1个）打散，用食用油（5.0g）翻炒，再加入虾皮（5.0g）翻炒30秒，倒入水（200g），加入鸡汤料（2.0g）、生抽（5.0g）、盐（2.0g）、糖（2.0g），大火煮开1分钟。」
- 禁止在步骤开头加"第X步："或"1."等序号
- 每步一句话，但要包含该步所用食材的克重

【任务 4：双语描述】
- en_name：英文菜名（简洁专业，适合西方读者）
- zh_desc：中文诗意简介（20-30字，突出口感/风味，适合菜单展示）
- en_desc：英文简介（20-30词，同等风格）

━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出 JSON】（不含 markdown 包裹）：
{{
  "name": "菜名（简洁4-8字）",
  "en_name": "English dish name",
  "zh_desc": "中文诗意简介",
  "en_desc": "English poetic description",
  "cooking_method": ["炒"等],
  "uses_wok": true/false,
  "prep_difficulty": "简单"|"中等"|"繁琐",
  "active_time_min": 整数（实操时间）,
  "idle_time_min": 整数（等待时间，无需操作）,
  "cuisine": "家常/川菜/日式/...",
  "is_parallel": false,
  "category": ["荤/素/荤素（可省）", "菜肴/主食/甜点/早餐/饮料/冷冻/预制"],
  "condiment_ratio": 0.0-1.0,
  "steps": ["步骤（内嵌食材克重）", ...],
  "ingredients": [
    {{
      "name": "食材名",
      "amount": 数值（amount_g换算后的数字）,
      "unit": "g",
      "is_condiment": true/false（液体调料/酱料/香料/芡粉=true；主料=false）
    }}
  ]
}}
"""


def _ai_parse_recipe(raw: str) -> tuple[dict, list]:
    """Call Gemini to parse free-form recipe text. Returns (recipe_data, ingredients)."""
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未设置，请在 .env 中配置")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _AI_PARSE_PROMPT.format(raw=raw)
    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )
    text = re.sub(r"^```(?:json)?\s*", "", resp.text.strip())
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    ingredients = parsed.pop("ingredients", [])
    parsed["data_quality"] = "needs_review"
    return parsed, ingredients


def _seed_new_ingredients(recipe_id: str) -> tuple[list, list]:
    """USDA-lookup non-condiment ingredients not yet in nutrition cache.
    Returns (found_names, still_missing_names)."""
    from db.nutrition import get_all_cached_names
    from utils.nutrition_lookup import lookup_ingredient

    ings   = get_ingredients(recipe_id)
    mains  = [i["name"] for i in ings if not i.get("is_condiment")]
    cached = set(get_all_cached_names())

    found, missing = [], []
    for name in mains:
        if name in cached:
            found.append(name)
            continue
        result = lookup_ingredient(name)
        (found if result else missing).append(name)
    return found, missing


def _view_ai_onboard() -> None:
    st.title("⚡ AI 快速入库")
    if st.button("← 返回菜谱库"):
        _goto_list()
        st.rerun()
    st.caption("粘贴你整理好的菜谱文字，AI 自动解析后一键入库并触发营养数据录入。")
    st.divider()

    # ── Step 1: text input ────────────────────────────────────
    if "ai_ob_recipe" not in st.session_state:
        if err := st.session_state.get("ai_ob_err"):
            st.error(err)
            if st.button("✕ 关闭错误提示", key="ai_ob_err_dismiss"):
                st.session_state.pop("ai_ob_err", None)
                st.rerun()
        st.subheader("① 粘贴菜谱文字")
        raw = st.text_area(
            "菜谱原文",
            height=220,
            key="ai_ob_raw_input",
            placeholder=(
                "180g肉末，250g豆腐干切丁，120g毛豆。葱姜蒜小米辣香菜。\n"
                "调料mix：料酒10g，生抽10g，老抽10g，味精1g，糖5g。\n"
                "不粘锅煸炒肉末出油，加葱姜蒜黄豆酱30g，小火翻炒，加调料mix+豆腐干+200ml水，"
                "炖煮5分钟，加毛豆，水淀粉收汁。"
            ),
            label_visibility="collapsed",
        )
        if st.button("🤖 AI 解析", type="primary", use_container_width=True, disabled=not raw.strip()):
            st.session_state.pop("ai_ob_err", None)
            with st.spinner("Gemini 正在解析菜谱…"):
                try:
                    recipe_data, ings = _ai_parse_recipe(raw.strip())
                    st.session_state["ai_ob_recipe"] = recipe_data
                    st.session_state["ai_ob_ings"]   = ings
                    st.session_state["ai_ob_raw"]    = raw.strip()
                except Exception as e:
                    # Stashed rather than rendered — the st.rerun() below discards
                    # this run's output, so an inline st.error() is never seen and
                    # a quota/JSON failure looks like the button did nothing.
                    st.session_state["ai_ob_err"] = f"解析失败：{e}"
            st.rerun()
        return

    # ── Step 2: preview & confirm ─────────────────────────────
    if "ai_ob_rid" not in st.session_state:
        recipe = st.session_state["ai_ob_recipe"]
        ings   = st.session_state["ai_ob_ings"]

        st.subheader("② 确认解析结果")
        st.caption("如有明显错误可返回修改原文重新解析；细节可入库后在编辑页调整。")

        c1, c2, c3 = st.columns(3)
        c1.metric("菜名", recipe.get("name", "—"))
        c2.metric("实操时间", f"{recipe.get('active_time_min', '?')} min")
        c3.metric("等待时间", f"{recipe.get('idle_time_min', 0)} min")

        if recipe.get("en_name"):
            st.caption(f"**{recipe['en_name']}**")
        if recipe.get("zh_desc"):
            st.caption(f"_{recipe['zh_desc']}_")
        if recipe.get("en_desc"):
            st.caption(f"_{recipe['en_desc']}_")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.caption(f"**难度** {recipe.get('prep_difficulty','—')}")
        m2.caption(f"**菜系** {recipe.get('cuisine','—')}")
        m3.caption(f"**分类** {' / '.join(recipe.get('category',[]))}")
        m4.caption(f"**烹饪方式** {' + '.join(recipe.get('cooking_method',[]))}")
        m5.caption(f"**调料摄入** {int(recipe.get('condiment_ratio',1)*100)}%")

        # Ingredients table
        main_ings = [i for i in ings if not i.get("is_condiment")]
        cond_ings = [i for i in ings if i.get("is_condiment")]

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**主料**")
            for i in main_ings:
                st.caption(f"• {i['name']}  {i['amount']}{i['unit']}")
        with col_b:
            st.markdown("**调料**")
            for i in cond_ings:
                st.caption(f"• {i['name']}  {i['amount']}{i['unit']}")

        if recipe.get("steps"):
            with st.expander("步骤预览", expanded=True):
                for j, s in enumerate(recipe["steps"], 1):
                    st.caption(f"{j}. {s}")

        st.divider()
        bc1, bc2 = st.columns(2)
        if bc2.button("✅ 确认入库", type="primary", use_container_width=True):
            rid = create_recipe(recipe, ings)
            invalidate_recipes_cache()
            st.session_state["ai_ob_rid"] = rid
            if _SEMANTIC_OK:
                try:
                    index_recipe(get_recipe(rid), get_ingredients(rid))
                except Exception:
                    pass
            st.rerun()
        if bc1.button("← 重新输入", use_container_width=True):
            for k in ("ai_ob_recipe", "ai_ob_ings", "ai_ob_raw"):
                st.session_state.pop(k, None)
            st.rerun()
        return

    # ── Step 3: nutrition seed ────────────────────────────────
    rid = st.session_state["ai_ob_rid"]
    recipe = get_recipe(rid)
    st.success(f"✅ **{recipe['name']}** 已入库！")

    st.subheader("③ 营养数据录入")
    st.caption("自动查询主料营养（SQLite 缓存 → USDA），未找到的可后续在食材库手动补充。")

    if st.button("🔍 触发营养查询", type="primary", use_container_width=True):
        with st.spinner("查询主料营养数据…"):
            found, missing = _seed_new_ingredients(rid)
        if found:
            st.success(f"✅ 已匹配 {len(found)} 种食材：{', '.join(found)}")
        if missing:
            st.warning(
                f"⚠️ {len(missing)} 种食材未找到数据：**{', '.join(missing)}**\n\n"
                "→ 可运行 `python3.9 scripts/seed_nutrition_ai.py "
                + " ".join(f'--ingredient {n}' for n in missing[:3])
                + ("…`" if len(missing) > 3 else "`")
                + " 补充"
            )

    st.divider()
    c1, c2 = st.columns(2)
    if c1.button("📝 继续编辑此菜谱", use_container_width=True):
        _goto_form("edit", rid)
        st.rerun()
    if c2.button("🍳 返回菜谱库", use_container_width=True):
        _goto_list()
        st.rerun()


def _view_list() -> None:
    hcol, bcol, aicol, nbcol = st.columns([4, 1, 1.2, 1.3])
    hcol.title("🍳 菜谱库")
    if bcol.button("➕ 新增", use_container_width=True):
        _goto_form("add")
        st.rerun()
    if aicol.button("⚡ AI 入库", use_container_width=True, type="primary"):
        _goto_ai_onboard()
        st.rerun()
    if nbcol.button("🗄️ 食材营养库", use_container_width=True):
        st.session_state["_nav_pending"] = "📊 营养分析"
        st.rerun()

    # ── Semantic search ──────────────────────────────────────
    if _SEMANTIC_OK:
        with st.expander("🔍 语义搜索（AI 相似菜谱）", expanded=False):
            sq_col, sb_col = st.columns([5, 1])
            sem_query = sq_col.text_input(
                "语义搜索", placeholder="例如：清淡低脂的蔬菜汤…",
                label_visibility="collapsed", key="sem_query",
            )
            sem_topk = sb_col.number_input(
                "数量", min_value=1, max_value=20, value=5,
                label_visibility="collapsed", key="sem_topk",
            )
            idx_count = get_indexed_count()
            st.caption(f"索引中共 {idx_count} 道菜谱")
            if idx_count == 0:
                if st.button("🗂️ 初始化语义索引", use_container_width=True):
                    with st.spinner("正在建立嵌入索引，首次约需1分钟…"):
                        n = index_all_recipes()
                    st.success(f"✅ 已索引 {n} 道菜谱")
                    st.rerun()
            elif sem_query.strip():
                results = semantic_search(sem_query.strip(), top_k=int(sem_topk))
                if results:
                    for hit in results:
                        sc_col, nm_col = st.columns([1, 6])
                        sc_col.metric("相似度", f"{hit['score']:.0%}")
                        if nm_col.button(hit["name"], key=f"sem_{hit['id']}"):
                            _goto_detail(hit["id"])
                            st.rerun()
                else:
                    st.info("未找到相似菜谱，尝试更换描述词。")
            if idx_count > 0:
                if st.button("🔄 重建全量索引", use_container_width=False, key="sem_rebuild"):
                    with st.spinner("重建中…"):
                        n = index_all_recipes()
                    st.success(f"✅ 已重建，共 {n} 道")
                    st.rerun()

    # Filters
    fc1, fc2, fc3, fc4, fc5 = st.columns([2.5, 2, 2, 2, 2])
    f_search  = fc1.text_input(
        "搜索", placeholder="🔍 搜索菜名…", label_visibility="collapsed",
    )
    f_meat    = fc2.selectbox(
        "荤素", ["全部"] + CATEGORIES_MEAT, label_visibility="collapsed",
    )
    f_form    = fc3.selectbox(
        "形态", ["全部"] + CATEGORIES_FORM, label_visibility="collapsed",
    )
    f_method  = fc4.selectbox(
        "烹饪方式", ["全部"] + METHODS, label_visibility="collapsed",
    )
    f_quality = fc5.selectbox(
        "数据质量",
        ["全部"] + list(QUALITY_LABEL.values()),
        label_visibility="collapsed",
    )

    qual_reverse = {v: k for k, v in QUALITY_LABEL.items()}
    q_filter = qual_reverse.get(f_quality)

    recipes = get_all_recipes(
        data_quality=q_filter,
        cooking_method=None if f_method == "全部" else f_method,
        search=f_search.strip() or None,
    )

    # Python-side category filter (supports multi-dimensional AND)
    if f_meat != "全部":
        recipes = [r for r in recipes if f_meat in r["category"]]
    if f_form != "全部":
        recipes = [r for r in recipes if f_form in r["category"]]

    st.caption(f"共 {len(recipes)} 道菜谱")
    st.divider()

    if not recipes:
        st.info("暂无匹配菜谱，点击右上角「➕ 新增」添加第一道菜。")
        return

    for r in recipes:
        rc1, rc2 = st.columns([5.5, 1])
        with rc1:
            ql = QUALITY_LABEL.get(r.get("data_quality", ""), "")
            wok  = " 🍳" if r.get("uses_wok")    else ""
            para = " ⚡" if r.get("is_parallel")  else ""
            t    = f"⏱{r['cook_time_min']}min" if r.get("cook_time_min") else ""
            st.markdown(f"**{r['name']}**  {ql}{wok}{para}")

            sub = []
            if r.get("category"):
                sub.append(_cat_badges(r["category"]))
            if r.get("cooking_method"):
                sub.append(_method_tags(r["cooking_method"]))
            if r.get("prep_difficulty"):
                sub.append(f"备菜:{r['prep_difficulty']}")
            if t:
                sub.append(t)
            if sub:
                st.caption("  |  ".join(sub))

        with rc2:
            if st.button("详情", key=f"det_{r['id']}", use_container_width=True):
                _goto_detail(r["id"])
                st.rerun()
        st.divider()


# ── Detail view ───────────────────────────────────────────────

def _view_detail() -> None:
    rid = st.session_state.get("rid")
    if not rid:
        _goto_list()
        st.rerun()
        return

    recipe = get_recipe(rid)
    if not recipe:
        st.error("菜谱不存在")
        if st.button("← 返回列表"):
            _goto_list()
            st.rerun()
        return

    ings       = get_ingredients(rid)
    main_ings  = [i for i in ings if not i["is_condiment"]]
    condiments = [i for i in ings if i["is_condiment"]]

    # Header row
    bk, _, ed, ck = st.columns([1, 3.5, 1.5, 1.5])
    if bk.button("← 返回"):
        _goto_list()
        st.rerun()
    if ed.button("✏️ 编辑", use_container_width=True):
        _goto_form("edit", rid)
        st.rerun()
    if ck.button("✅ 已做", use_container_width=True):
        mark_cooked(rid)
        invalidate_recipes_cache()
        st.success("已更新「上次烹饪」时间")
        st.rerun()

    st.title(recipe["name"])
    if recipe.get("en_name"):
        st.markdown(f"*{recipe['en_name']}*")
    if recipe.get("en_desc") or recipe.get("zh_desc"):
        desc_parts = []
        if recipe.get("zh_desc"):
            desc_parts.append(recipe["zh_desc"])
        if recipe.get("en_desc"):
            desc_parts.append(recipe["en_desc"])
        st.caption("  ·  ".join(desc_parts))
    if recipe.get("category"):
        st.markdown(_cat_badges(recipe["category"]))

    # Metadata row
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("备菜难度", recipe.get("prep_difficulty") or "—")
    active = recipe.get("active_time_min") or recipe.get("cook_time_min")
    idle   = recipe.get("idle_time_min") or 0
    time_str = f"{active}min" if active and not idle else (
               f"{active}+{idle}min" if active and idle else "—")
    m2.metric("实操+等待", time_str)
    m3.metric("占用炒锅", "是 🍳" if recipe.get("uses_wok") else "否")
    m4.metric("可并行",   "⚡ 是" if recipe.get("is_parallel") else "否")
    m5.metric("调料摄入", f"{int(float(recipe.get('condiment_ratio', 1.0)) * 100)}%")
    m6.metric("个人份量", f"{int(float(recipe.get('serving_ratio',   1.0)) * 100)}%")
    m7.metric("数据质量", QUALITY_LABEL.get(recipe.get("data_quality", ""), "—"))

    if recipe.get("cooking_method"):
        st.markdown("**烹饪方式：**" + _method_tags(recipe["cooking_method"]))
    if recipe.get("cuisine"):
        st.markdown(f"**菜系：** :blue-background[{recipe['cuisine']}]")
    if recipe.get("tags"):
        st.markdown("**标签：**" + "  ".join(f"`{t}`" for t in recipe["tags"]))
    if recipe.get("source_url"):
        st.markdown(f"**来源：** {recipe['source_url']}")
    if recipe.get("last_cooked"):
        st.caption(f"上次烹饪：{recipe['last_cooked']}")

    # Pairings — clickable chips
    pairing_ids = recipe.get("pairing_ids") or []
    if pairing_ids:
        st.markdown("**🍱 强搭配：**")
        cols = st.columns(min(len(pairing_ids), 4))
        for i, pid in enumerate(pairing_ids):
            p = get_recipe(pid)
            if not p: continue
            if cols[i % len(cols)].button(p["name"], key=f"pair_{pid}", use_container_width=True):
                _goto_detail(pid)
                st.rerun()

    # Ingredients
    st.divider()
    col_m, col_c = st.columns(2)

    with col_m:
        st.subheader("🥩 主料")
        if main_ings:
            for ing in main_ings:
                st.write(f"• {ing['name']}：**{ing['amount']}{ing.get('unit','g')}**")
        else:
            st.caption("（暂无主料）")

    with col_c:
        st.subheader("🧂 调料")
        if condiments:
            for ing in condiments:
                st.write(f"• {ing['name']}：**{ing['amount']}{ing.get('unit','g')}**")
        else:
            st.caption("（暂无调料）")

    if recipe.get("steps"):
        st.divider()
        st.subheader("📋 烹饪步骤")
        for i, step in enumerate(recipe["steps"], 1):
            st.markdown(f"**{i}.** {step}")

    if recipe.get("notes"):
        st.divider()
        st.subheader("📝 备注")
        st.info(recipe["notes"])

    # Delete
    st.divider()
    if st.session_state.get("confirm_delete"):
        st.error("确认删除此菜谱？此操作不可撤销。")
        dc1, dc2 = st.columns(2)
        if dc1.button("🗑️ 确认删除", type="primary", use_container_width=True):
            delete_recipe(rid)
            invalidate_recipes_cache()
            _goto_list()
            st.rerun()
        if dc2.button("取消", use_container_width=True):
            st.session_state["confirm_delete"] = False
            st.rerun()
    else:
        if st.button("🗑️ 删除菜谱"):
            st.session_state["confirm_delete"] = True
            st.rerun()


# ── Form view ─────────────────────────────────────────────────

def _view_form() -> None:
    mode = st.session_state.get("rmode", "add")
    rid  = st.session_state.get("rid")

    bk, th = st.columns([1, 6])
    if bk.button("← 返回"):
        if mode == "edit" and rid:
            _goto_detail(rid)
        else:
            _goto_list()
        st.rerun()
    th.subheader("✏️ 编辑菜谱" if mode == "edit" else "➕ 新增菜谱")

    st.divider()

    # ── Basic info ────────────────────────────────────────
    st.subheader("基本信息")
    st.text_input("菜名 *", key="rf_name")
    st.text_input("下厨房链接（可选）", key="rf_url")

    col1, col2, col3 = st.columns(3)
    col1.selectbox("备菜难度", DIFFICULTIES, key="rf_difficulty")
    col2.number_input("实操时间（分钟）", min_value=0, max_value=300, step=5,
                      key="rf_active_time",
                      help="需要持续操作的时间：切菜、翻炒、调味等")
    col3.number_input("等待时间（分钟）", min_value=0, max_value=600, step=5,
                      key="rf_idle_time",
                      help="不需要看守的时间：腌制、炖煮、蒸制、发酵等")

    st.multiselect("烹饪方式（可多选）", METHODS, key="rf_methods")

    col3, col4 = st.columns(2)
    col3.checkbox("占用炒锅 🍳", key="rf_wok")
    col4.checkbox("可与其他菜并行 ⚡（炖/蒸/烤=是）", key="rf_parallel")

    # ── Category ──────────────────────────────────────────
    st.divider()
    st.subheader("分类标签")
    st.caption("荤素维度可留空（推荐填）；形态维度选择菜谱类型（影响推荐算法，菜肴/主食才会出现在晚餐推荐中）。")

    col5, col6 = st.columns(2)
    col5.multiselect("荤素（可留空）", CATEGORIES_MEAT, key="rf_cat_meat", max_selections=1)
    col6.selectbox(
        "形态 *",
        [""] + CATEGORIES_FORM,
        key="rf_cat_form",
        format_func=lambda x: x or "（请选择）",
    )

    col7, col8 = st.columns(2)
    col7.text_input("自由标签（逗号分隔，如：快手, 家常）", key="rf_tags")
    col8.selectbox(
        "数据质量",
        list(QUALITY_LABEL.keys()),
        format_func=lambda x: QUALITY_LABEL[x],
        key="rf_quality",
    )
    st.text_area("个人备注（可选）", key="rf_notes", height=80)

    # ── Cuisine & Pairings ───────────────────────────────
    st.divider()
    st.subheader("🍱 菜系 & 强搭配")
    st.caption("菜系用于推荐器同菜系联动（生成「日式定食」风格的整餐）；搭配菜谱在你选这道菜时会自动出现在「推荐搭配」提示中。")

    cc1, _ = st.columns([1, 2])
    cc1.selectbox(
        "菜系",
        CUISINES,
        key="rf_cuisine",
        format_func=lambda x: x or "（未设置）",
    )

    # Pairing multi-select: exclude current recipe being edited
    current_rid = st.session_state.get("rid") if mode == "edit" else None
    pairing_pool = [r for r in get_all_recipes() if r["id"] != current_rid]
    pairing_label = {r["id"]: r["name"] for r in pairing_pool}
    st.multiselect(
        "强搭配菜谱（选中本菜时自动建议）",
        options=list(pairing_label.keys()),
        format_func=lambda rid: pairing_label.get(rid, rid),
        key="rf_pairing_ids",
        placeholder="搜索菜名，可多选…",
    )

    # ── Restaurant menu fields ─────────────────────────────
    st.divider()
    st.subheader("🍽️ 菜单描述（用于打印菜单，可由 AI 生成）")
    st.text_input("英文菜名（如 Braised Pork Belly with Taro）", key="rf_en_name")
    st.text_input("英文简介（1句话，如 A silky braise with the earthy sweetness of taro）", key="rf_en_desc")
    st.text_input("中文简介（1句话，可选，如 软糯入味，入口即化）", key="rf_zh_desc")

    # ── Steps ─────────────────────────────────────────────
    st.divider()
    st.subheader("📋 烹饪步骤（可选）")
    st.caption("每行一步，打印菜单时自动编号显示。")
    st.text_area(
        "烹饪步骤",
        key="rf_steps",
        height=180,
        placeholder="热锅凉油，下葱姜蒜爆香\n加入主料翻炒至变色\n加调料调味，大火收汁",
        label_visibility="collapsed",
    )

    # ── Condiment ratio + Serving ratio ──────────────────
    st.divider()
    st.slider(
        "调料摄入比例（本菜谱所有调料）",
        min_value=0, max_value=100,
        key="rf_condiment_ratio",
        step=5, format="%d%%",
        help="调料实际被吃进去的比例。炒蔬菜/盐腌：100%；蒸鱼/腌料：50%；红烧/炖肉（汤汁不喝）：15–25%；涮锅/汤底：5–15%。",
    )
    st.slider(
        "个人摄入份量（每人实际吃多少）",
        min_value=0, max_value=100,
        key="rf_serving_ratio",
        step=5, format="%d%%",
        help="相对于菜谱总量 ÷ 2 的个人摄入比。50% = 正常一人份（总量÷2再取一半）；100% = 吃满半锅。",
    )

    # ── Ingredients ───────────────────────────────────────
    st.divider()
    st.subheader("食材列表")

    hc1, hc2, hc3, hc4, hc5 = st.columns([3, 1.5, 1.8, 1.5, 0.7])
    hc1.caption("食材名")
    hc2.caption("用量")
    hc3.caption("单位")
    hc4.caption("调料？")

    ids = st.session_state.get("draft_ing_ids", [])
    for sid in list(ids):
        _ingredient_slot(sid)

    if st.button("＋ 添加食材行"):
        ctr = st.session_state.get("ing_counter", 0)
        sid = f"i{ctr}"
        st.session_state[f"ing_{sid}_name"]  = ""
        st.session_state[f"ing_{sid}_amt"]   = 0.0
        st.session_state[f"ing_{sid}_unit"]  = "g"
        st.session_state[f"ing_{sid}_cond"]  = False
        st.session_state["draft_ing_ids"]    = ids + [sid]
        st.session_state["ing_counter"]      = ctr + 1
        st.rerun()

    # ── Save ──────────────────────────────────────────────
    st.divider()
    if st.button("💾 保存菜谱", type="primary", use_container_width=True):
        name = (st.session_state.get("rf_name") or "").strip()
        if not name:
            st.error("菜名不能为空")
            st.stop()

        ingredient_list = []
        for sid in st.session_state.get("draft_ing_ids", []):
            ing_name = (st.session_state.get(f"ing_{sid}_name") or "").strip()
            if not ing_name:
                continue
            is_cond = bool(st.session_state.get(f"ing_{sid}_cond", False))
            ingredient_list.append({
                "name":         ing_name,
                "amount":       float(st.session_state.get(f"ing_{sid}_amt") or 0),
                "unit":         st.session_state.get(f"ing_{sid}_unit", "g"),
                "is_condiment": is_cond,
                # Always 1.0 — the per-ingredient knob is retired. 调料摄入比例
                # lives on the recipe as condiment_ratio (this page's 滑块).
                "intake_ratio": 1.0,
            })

        tags_raw = st.session_state.get("rf_tags", "")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        form_val = st.session_state.get("rf_cat_form") or ""
        cat  = list(st.session_state.get("rf_cat_meat") or [])
        if form_val:
            cat.append(form_val)

        steps_raw = st.session_state.get("rf_steps", "")
        steps = [s.strip() for s in steps_raw.splitlines() if s.strip()]

        recipe_data = {
            "name":             name,
            "source_url":       st.session_state.get("rf_url") or None,
            "cooking_method":   st.session_state.get("rf_methods", []),
            "uses_wok":         bool(st.session_state.get("rf_wok")),
            "prep_difficulty":  st.session_state.get("rf_difficulty", "中等"),
            "active_time_min":  int(st.session_state.get("rf_active_time") or 0),
            "idle_time_min":    int(st.session_state.get("rf_idle_time")   or 0),
            "is_parallel":      bool(st.session_state.get("rf_parallel")),
            "category":         cat,
            "tags":             tags,
            "data_quality":     st.session_state.get("rf_quality", "needs_review"),
            "notes":            st.session_state.get("rf_notes") or None,
            "condiment_ratio":  st.session_state.get("rf_condiment_ratio", 100) / 100,
            "serving_ratio":    st.session_state.get("rf_serving_ratio",   100) / 100,
            "cuisine":          st.session_state.get("rf_cuisine") or None,
            "pairing_ids":      list(st.session_state.get("rf_pairing_ids") or []),
            "steps":            steps,
            "en_name":          st.session_state.get("rf_en_name") or None,
            "en_desc":          st.session_state.get("rf_en_desc") or None,
            "zh_desc":          st.session_state.get("rf_zh_desc") or None,
        }

        if mode == "edit" and rid:
            update_recipe(rid, recipe_data, ingredient_list)
            invalidate_recipes_cache()
            if _SEMANTIC_OK:
                recipe_data["id"] = rid
                index_recipe(recipe_data, ingredient_list)
            _goto_detail(rid)
        else:
            new_id = create_recipe(recipe_data, ingredient_list)
            invalidate_recipes_cache()
            if _SEMANTIC_OK:
                recipe_data["id"] = new_id
                index_recipe(recipe_data, ingredient_list)
            _goto_detail(new_id)
        st.rerun()


# ── Entry point ───────────────────────────────────────────────

def show() -> None:
    if "rv" not in st.session_state:
        st.session_state["rv"] = "list"

    view = st.session_state["rv"]
    if view == "list":
        _view_list()
    elif view == "detail":
        _view_detail()
    elif view == "form":
        _view_form()
    elif view == "ai_onboard":
        _view_ai_onboard()
    else:
        _goto_list()
        st.rerun()
