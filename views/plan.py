"""Phase 5: 今日食谱规划 — 智能推荐 + 手动选菜 + Placeholder + 实时营养 + 库存扣减"""
import re
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from db.init_db import get_connection
from db.inventory import get_all_inventory, set_quantity
from db.recipes import get_all_recipes, get_recipe, get_ingredients, mark_cooked
from utils.nutrition_lookup import calc_nutrition_with_breakdown, to_grams
from utils.recommender import recommend
from utils.pdf_generator import generate_daily_menu_pdf, open_pdf
from views.nutrition import get_pdf_nutrition_dict, _FRUITS_LIST

# ── Session state keys ────────────────────────────────────────
_RIDS    = "plan_rids"     # list[str]  — selected recipe IDs (ordered)
_PH      = "plan_ph"       # list[dict] — placeholder ingredient dicts
_CONFIRM = "plan_confirm"  # bool       — show deduction confirmation panel
_COMBOS  = "plan_combos"   # list[dict] | None — recommender output cache

# ── Category / method constants ───────────────────────────────
_CATS_MEAT = ["纯蛋白", "半蛋白半素", "纯素"]
_CATS_FORM = ["菜肴", "主食", "甜点", "早餐", "饮料", "冷冻", "预制"]
_METHODS   = ["炒", "蒸", "烤", "煮", "汤", "凉拌", "炸", "炖", "煎"]
_CAT_COLOR = {
    "纯蛋白": "red", "半蛋白半素": "orange", "纯素": "green",
    "菜肴": "blue", "主食": "orange", "甜点": "violet",
    "早餐": "yellow", "饮料": "cyan", "冷冻": "gray", "预制": "gray",
}


# ── Init ─────────────────────────────────────────────────────

def _init() -> None:
    for key, default in [(_RIDS, []), (_PH, []), (_CONFIRM, False), (_COMBOS, None)]:
        if key not in st.session_state:
            st.session_state[key] = default


# ── Small helpers ─────────────────────────────────────────────

def _cat_txt(cats: list) -> str:
    return "  ".join(f":{_CAT_COLOR.get(c, 'gray')}[{c}]" for c in cats)


def _parse_ph(text: str) -> list:
    """Parse free-form placeholder text → list of ingredient dicts."""
    result = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(.+?)\s+([\d.]+)\s*([a-zA-Z一-鿿]+)?', line)
        if not m:
            continue
        try:
            amt = float(m.group(2))
        except ValueError:
            continue
        result.append({
            "name":         m.group(1).strip(),
            "amount":       amt,
            "unit":         (m.group(3) or "g").strip(),
            "intake_ratio": 1.0,
        })
    return result


def _protein_target() -> tuple:
    try:
        conn = get_connection()
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM user_settings"
        ).fetchall()}
        conn.close()
        wa = float(rows.get("weight_a", 65))
        wb = float(rows.get("weight_b", 55))
        mn = float(rows.get("protein_multiplier_min", 1.2))
        mx = float(rows.get("protein_multiplier_max", 1.5))
        return (wa + wb) * mn, (wa + wb) * mx
    except Exception:
        return 144.0, 180.0


# ── Constraint checks ─────────────────────────────────────────

def _wok_violations(recipes: list) -> list:
    std   = [r for r in recipes if r.get("uses_wok") and (r.get("active_time_min") or r.get("cook_time_min") or 99) > 5]
    light = [r for r in recipes if r.get("uses_wok") and (r.get("active_time_min") or r.get("cook_time_min") or 99) <= 5]
    msgs  = []
    if len(std) > 1:
        names = "」「".join(r["name"] for r in std)
        msgs.append(f"🍳 炒锅冲突：「{names}」均需标准占锅（>5min），最多选 1 道")
    if len(light) > 1:
        names = "」「".join(r["name"] for r in light)
        msgs.append(f"⚡ 轻占锅过多：「{names}」均为快手炒菜（≤5min），最多 1 道")
    return msgs


def _uncovered_perishables(recipe_ids: list) -> list:
    inv = get_all_inventory()
    urgent = [
        i["name"] for i in inv.get("leafy_veg", [])
        if i.get("is_perishable") and float(i.get("quantity") or 0) > 0
    ]
    if not urgent:
        return []
    used: set = set()
    for rid in recipe_ids:
        for ing in get_ingredients(rid):
            used.add(ing["name"])
    return [p for p in urgent if p not in used]


def _boredom_violations(recipe_ids: list) -> list:
    cutoff = datetime.now() - timedelta(hours=48)
    recent = []
    for rid in recipe_ids:
        r = get_recipe(rid)
        if not r or not r.get("last_cooked"):
            continue
        try:
            lc = datetime.strptime(r["last_cooked"], "%Y-%m-%d %H:%M:%S")
            if lc > cutoff:
                recent.append(r["name"])
        except ValueError:
            pass
    return recent


# ── Nutrition preview (Remains based on exact Grams) ──────────

def _build_ings(recipe_ids: list, ph: list, recipes_by_id: dict) -> list:
    result = []
    for rid in recipe_ids:
        recipe    = recipes_by_id.get(rid, {})
        cond_r    = float(recipe.get("condiment_ratio") or 1.0)
        serving_r = float(recipe.get("serving_ratio")   or 1.0)
        for ing in get_ingredients(rid):
            # 【修复】同步加入 intake_ratio 判断逻辑
            base_ratio = float(ing.get("intake_ratio") if ing.get("intake_ratio") is not None else 1.0)
            ratio = (base_ratio * cond_r) if ing.get("is_condiment") else base_ratio
            
            result.append({
                "name":         ing["name"],
                "amount":       float(ing.get("amount") or 0) * serving_r,
                "unit":         ing.get("unit", "g"),
                "intake_ratio": ratio,
            })
    result.extend(ph)
    return result


def _nutrition_preview(recipe_ids: list, ph: list, recipes_by_id: dict) -> None:
    if not recipe_ids and not ph:
        st.caption("选择菜谱后实时显示营养预估")
        return

    ings   = _build_ings(recipe_ids, ph, recipes_by_id)
    n, bds = calc_nutrition_with_breakdown(ings)

    p_min, p_max = _protein_target()
    pp = 2.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("热量 / 人",   f"{n.kcal/pp:.0f} kcal")
    c2.metric("蛋白质 / 人", f"{n.protein/pp:.1f} g")
    c3.metric("脂肪 / 人",   f"{n.fat/pp:.1f} g")
    c4.metric("钠 / 人",     f"{n.sodium/pp:.0f} mg")

    if n.found > 0:
        prot_pp = n.protein / pp
        pct = min(prot_pp / (p_max / pp), 1.0) if p_max > 0 else 0.0
        dot = "🟡" if prot_pp < p_min / pp else ("🟢" if prot_pp <= p_max / pp else "🔵")
        st.progress(pct, text=f"{dot} 蛋白质/人 {prot_pp:.1f}g  目标 {p_min/pp:.0f}–{p_max/pp:.0f}g")

    sod_pp = n.sodium / pp
    if sod_pp > 2300:
        st.error(f"🚨 钠超标：每人 {sod_pp:.0f}mg（上限 2300mg）")
    elif sod_pp > 1900:
        st.warning(f"⚠️ 钠略高：每人 {sod_pp:.0f}mg，注意调料用量")

    if bds:
        with st.expander(f"📋 食材明细（{n.found} 种已匹配）"):
            st.dataframe(
                bds,
                column_config={"USDA": st.column_config.LinkColumn("USDA", display_text="🔗")},
                use_container_width=True,
                hide_index=True,
            )


# ── Inventory deduction helpers (Portion logic) ───────────────

def _compute_deductions(recipe_ids: list) -> tuple:
    inv = get_all_inventory()
    item_map: dict = {}
    for cat in ("leafy_veg", "protein", "seasoning", "dry_goods", "other"):
        for item in inv.get(cat, []):
            item_map[item["name"]] = item

    usage_names: set = set()

    for rid in recipe_ids:
        for ing in get_ingredients(rid):
            if not ing.get("is_condiment"):
                usage_names.add(ing["name"])

    deductions    = []
    used_booleans = []
    unmatched     = []

    for name in usage_names:
        if name in item_map:
            item = item_map[name]
            if item.get("item_type") == "quantity":
                cur_qty = float(item.get("quantity") or 0)
                deductions.append({
                    "name":        name,
                    "item_id":     item["id"],
                    "current_qty": cur_qty,
                })
            else:
                used_booleans.append(name)
        else:
            unmatched.append(name)

    return deductions, used_booleans, unmatched


# ── UI sections ───────────────────────────────────────────────

def _section_recommend() -> None:
    c_btn, c_budget, c_info = st.columns([2, 1.5, 4])
    budget = c_budget.number_input(
        "⏰ 单菜上限(min)",
        min_value=15, max_value=300, value=90, step=15,
        key="rec_budget_min",
        help="排除总时长（实操+等待）超过此预算的菜。默认 90min ≈ 5pm 开始 6:30pm 开饭。"
             "周末想炖大菜可调到 180+。",
    )
    if c_btn.button("🎲 今日推荐", type="primary", use_container_width=True):
        from views.wishlist import get_active_wishlist_recipe_ids
        with st.spinner("正在生成推荐菜单…"):
            combos = recommend(
                n_combos=2, combo_size=4,
                max_single_dish_min=int(budget),
                wishlist_boost_ids=get_active_wishlist_recipe_ids(),
            )
        st.session_state[_COMBOS] = combos

    combos = st.session_state.get(_COMBOS)
    if combos is None:
        c_info.caption("点击按钮，根据库存自动推荐今日菜单（不含 48h 内刚做过的菜）")
        return
    if not combos:
        st.warning("⚠️ 暂无合适的推荐组合。")
        return

    cols = st.columns(len(combos))
    for col, combo in zip(cols, combos):
        recipes = combo["recipes"]
        stats   = combo["stats"]
        with col:
            with st.container(border=True):
                for i, r in enumerate(recipes):
                    icons = ("🍳 " if r.get("uses_wok") else "") + ("⚡" if r.get("is_parallel") else "")
                    t = f" {r['cook_time_min']}min" if r.get("cook_time_min") else ""
                    st.markdown(f"**{i+1}. {r['name']}** {icons}{t}")
                    if r.get("category"):
                        st.caption(" · ".join(r["category"]))

                st.divider()

                par_note = f"⚡ 并行约 **{stats['par_time']}min**" if stats["has_parallel"] else f"⏱️ 约 **{stats['seq_time']}min**"
                st.caption(f"{par_note}　难度：{stats['difficulty']}")

                if stats["has_soup"]:
                    st.caption("🍲 含汤")
                if stats.get("dominant_cuisine"):
                    st.caption(f"🍱 菜系联动：**{stats['dominant_cuisine']}**")
                if combo["covers_perishables"]:
                    st.caption("🔴 覆盖易坏：" + "、".join(combo["covers_perishables"]))
                for w in combo["warnings"]:
                    st.warning(w, icon="⚠️")

                if st.button("✅ 采用此套餐", key=f"adopt_{recipes[0]['id']}", use_container_width=True):
                    st.session_state[_RIDS]    = [r["id"] for r in recipes]
                    st.session_state[_CONFIRM] = False
                    st.session_state[_COMBOS]  = None
                    st.rerun()


def _sort_for_menu(recipes: list) -> list:
    def _key(r):
        methods = r.get("cooking_method") or []
        if "凉拌" in methods: return 0
        if "汤"   in methods: return 2
        return 1
    return sorted(recipes, key=_key)


def _print_pdf(recipes_by_id: dict) -> None:
    rids = st.session_state.get(_RIDS, [])
    recipes = _sort_for_menu([recipes_by_id[r] for r in rids if r in recipes_by_id])
    if not recipes:
        st.warning("菜单为空，无法生成 PDF。")
        return

    all_ings = {r["id"]: get_ingredients(r["id"]) for r in recipes}

    with st.spinner("计算全日营养并生成 PDF…"):
        nutr = get_pdf_nutrition_dict()   # JIT — pulls live session state
        path = generate_daily_menu_pdf(recipes, all_ings, nutr)
    open_pdf(path)
    st.success(f"✅ PDF 已生成并打开：`{path}`")


def _print_control_center() -> None:
    """Cross-page state console — same keys as views/nutrition.py.
    Streamlit binds widgets with identical keys to the same session_state slot,
    so edits here flow into the 营养分析 tab automatically and vice versa.
    """
    with st.expander("🎛️ 备餐控制台（与全日营养共享设置）", expanded=False):
        st.caption("修改这里的主食 / 水果 / 临时加菜会即时同步到「📊 营养分析 → 全日营养」标签，"
                   "也会作为 PDF 打印时营养计算的最新依据。")

        # ── Staple ───────────────────────────────────────────
        st.markdown("**🍚 晚餐主食（每人）**")
        st.radio("主食", ["🍚 白米饭", "🚫 不吃主食", "✏️ 自定义"],
                 horizontal=True, key="fd_staple_choice", label_visibility="collapsed")
        staple_choice = st.session_state.get("fd_staple_choice", "🍚 白米饭")
        if staple_choice == "🍚 白米饭":
            st.number_input("克数（熟米饭，每人）", min_value=50, max_value=500,
                            value=100, step=10, key="fd_staple_g")
        elif staple_choice == "✏️ 自定义":
            st.text_area("主食食材（每人份）", height=70, key="fd_staple_custom_txt",
                         label_visibility="collapsed",
                         placeholder="意大利面（熟）150 g\n乌冬面（熟）200 g")

        # ── Fruit ────────────────────────────────────────────
        st.markdown("**🍎 今日水果（每种 60–70g）**")
        fc1, fc2 = st.columns([4, 1])
        fc1.multiselect("今日水果", _FRUITS_LIST, default=["苹果", "香蕉", "蓝莓"],
                        key="fd_fruits", label_visibility="collapsed")
        fc2.number_input("每种(g)", min_value=30, max_value=200, value=65, step=5,
                         key="fd_fruit_g")

        # ── Dinner add-ons ───────────────────────────────────
        st.markdown("**➕ 晚餐临时加菜（总克数，会 ÷2 算每人份）**")
        st.text_area("加菜食材", height=70, key="fd_dinner_addons_txt",
                     label_visibility="collapsed",
                     placeholder="莴笋 150 g\n金针菇 100 g")


def _section_menu(recipes_by_id: dict) -> None:
    rids = st.session_state[_RIDS]
    ph   = st.session_state[_PH]

    h_col, btn_col = st.columns([5, 2])
    h_col.subheader("📋 今日菜单")
    if rids:
        if btn_col.button("🖨️ 打印今日菜单", use_container_width=True):
            _print_pdf(recipes_by_id)

    if rids:
        _print_control_center()

    if not rids and not ph:
        st.info("从下方菜谱库选择菜谱，或用「临时占位菜」直接输入食材。")
        return

    selected = [recipes_by_id[r] for r in rids if r in recipes_by_id]

    for i, r in enumerate(selected):
        c1, c2, c3 = st.columns([4.5, 2.5, 0.8])
        wok  = " 🍳" if r.get("uses_wok")   else ""
        para = " ⚡" if r.get("is_parallel") else ""
        t    = f" ({r['cook_time_min']}min)" if r.get("cook_time_min") else ""
        c1.markdown(f"**{i+1}. {r['name']}**{wok}{para}{t}")
        if r.get("category"):
            c2.markdown(_cat_txt(r["category"]))
        if c3.button("✕", key=f"rm_{r['id']}"):
            st.session_state[_RIDS].remove(r["id"])
            st.session_state[_CONFIRM] = False
            st.rerun()

    if ph:
        st.caption(f"*✏️ 临时占位菜：{len(ph)} 种食材*")

    for msg in _wok_violations(selected):
        st.error(msg)

    bored = _boredom_violations(rids)
    if bored:
        st.warning(f"🔄 防厌倦：「{'、'.join(bored)}」48小时内刚做过，建议换菜")

    uncov = _uncovered_perishables(rids)
    if uncov:
        st.warning(f"🔴 易坏优先：**{'、'.join(uncov)}** 库存充足但今日菜单未消耗，建议加菜")


def _section_confirm(recipes_by_id: dict) -> None:
    rids = st.session_state[_RIDS]
    if not rids:
        return

    st.divider()

    if not st.session_state[_CONFIRM]:
        if st.button("✅ 确认菜单并扣减库存", type="primary", use_container_width=True):
            st.session_state[_CONFIRM] = True
            st.rerun()
        return

    deductions, used_booleans, unmatched = _compute_deductions(rids)
    names = [recipes_by_id[r]["name"] for r in rids if r in recipes_by_id]

    st.subheader("🗃️ 扣减提议 (按份)")
    st.caption(f"菜单：{' + '.join(names)}\n\n系统默认扣减 1 份，请根据实际消耗量微调：")

    deduct_inputs = {}

    if deductions:
        with st.container(border=True):
            for row in deductions:
                c1, c2, c3 = st.columns([3, 2, 3])
                c1.write(f"**{row['name']}**")
                c2.write(f"当前: {row['current_qty']:.0f} 份")
                
                default_deduct = 1.0 if row['current_qty'] >= 1 else 0.0
                
                deduct_inputs[row['item_id']] = {
                    "current": row['current_qty'],
                    "deduct": c3.number_input(
                        "本次扣减",
                        min_value=0.0,
                        value=default_deduct,
                        step=0.5,
                        key=f"plan_deduct_{row['item_id']}",
                        label_visibility="collapsed"
                    )
                }
    else:
        st.info("未匹配到需要按份扣减的食材。")

    if used_booleans:
        st.success(f"🛒 常备免记量食材 (无需手动扣减): **{'、'.join(used_booleans)}**")

    if unmatched:
        st.caption(f"ℹ️ 提示：以下主料在库存中无条目：**{'、'.join(unmatched)}**")

    st.caption("📌 确认后会自动：① 扣减库存份数  ② 标记菜谱已做（48h 防厌倦生效）  ③ 保存今日营养记录到 📊 历史")

    bc1, bc2 = st.columns(2)
    if bc1.button("✅ 确认并结束", type="primary", use_container_width=True):
        # 1. Auto-save nutrition log FIRST (while plan_rids is still populated,
        #    since compute_fullday_silent reads it from session_state)
        nutrition_msg = ""
        try:
            from views.nutrition import compute_fullday_silent
            from db.daily_log import save_daily_log
            fd = compute_fullday_silent()
            today = datetime.now().strftime("%Y-%m-%d")
            save_daily_log(
                today, fd["total"], rids,
                list(st.session_state.get("fd_fruits", [])),
                int(st.session_state.get("fd_fruit_g", 65)),
                bfst_skip=fd.get("bfst_skip", False),
                bfst_custom_ings=fd.get("bfst_custom_ings") or [],
                lunch_skip=fd.get("lunch_skip", False),
                lunch_custom_ings=fd.get("lunch_custom_ings") or [],
                staple_ings=fd.get("staple_ings") or [],
                ingredients_snapshot=fd.get("snapshot") or [],
            )
            nutrition_msg = " · 营养已记录"
        except Exception as e:
            nutrition_msg = f" · ⚠️ 营养记录失败：{e}"

        # 2. Deduct inventory
        for item_id, vals in deduct_inputs.items():
            if vals["deduct"] > 0:
                set_quantity(item_id, max(0.0, vals["current"] - vals["deduct"]))

        # 3. Mark recipes as cooked (last_cooked timestamp → 48h 防厌倦)
        for rid in rids:
            mark_cooked(rid)

        # 4. Auto-remove these recipes from 🌌 食愿之书 (done = wish fulfilled)
        wishlist_removed = 0
        try:
            from views.wishlist import remove_by_recipe_ids
            wishlist_removed = remove_by_recipe_ids(rids)
        except Exception:
            pass

        st.session_state[_RIDS]    = []
        st.session_state[_PH]      = []
        st.session_state[_CONFIRM] = False
        wl_msg = f" · 🌌 食愿之书消去 {wishlist_removed} 道" if wishlist_removed else ""
        st.success(f"✅ 库存份数已更新{nutrition_msg}{wl_msg}")
        st.balloons()
        st.rerun()

    if bc2.button("← 取消", use_container_width=True):
        st.session_state[_CONFIRM] = False
        st.rerun()


def _build_avail_set(inv: dict) -> set:
    available: set = set()
    for item in inv.get("leafy_veg", []):
        if (item.get("quantity") or 0) > 0 or item.get("in_stock"):
            available.add(item["name"])
    for item in inv.get("protein", []):
        if (item.get("quantity") or 0) > 0 or item.get("in_stock"):
            available.add(item["name"])
    for cat in ("dry_goods", "seasoning", "other"):
        for item in inv.get(cat, []):
            if item.get("in_stock"):
                available.add(item["name"])
    return available


def _fetch_all_main_ings() -> dict[str, list[str]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT recipe_id, name FROM ingredients WHERE is_condiment = 0"
    ).fetchall()
    conn.close()
    result: dict = {}
    for row in rows:
        result.setdefault(row["recipe_id"], []).append(row["name"])
    return result


def _can_make(recipe_id: str, main_ings: dict, available: set) -> bool:
    ings = main_ings.get(recipe_id, [])
    if not ings:
        return True
    return all(name in available for name in ings)


def _section_picker(all_recipes: list, recipes_by_id: dict) -> None:
    current = set(st.session_state[_RIDS])

    # ── Pairing suggestions ──────────────────────────────────
    if current:
        suggested: dict = {}  # rid → anchor_name
        for rid in current:
            r = recipes_by_id.get(rid, {})
            for pid in (r.get("pairing_ids") or []):
                if pid in current or pid in suggested: continue
                if pid in recipes_by_id:
                    suggested[pid] = r["name"]
        if suggested:
            with st.container(border=True):
                st.markdown("**💡 推荐搭配**")
                cols = st.columns(min(len(suggested), 4))
                for i, (pid, anchor) in enumerate(suggested.items()):
                    p = recipes_by_id[pid]
                    if cols[i % len(cols)].button(
                        f"＋ {p['name']}  _(搭配 {anchor})_",
                        key=f"pair_add_{pid}", use_container_width=True,
                    ):
                        st.session_state[_RIDS].append(pid)
                        st.rerun()

    # ── Cuisine auto-anchor hint ──────────────────────────────
    anchor_cuisines = {
        recipes_by_id[rid].get("cuisine")
        for rid in current if recipes_by_id.get(rid, {}).get("cuisine")
    }
    if anchor_cuisines:
        st.caption(f"🍱 已锁定菜系：{'、'.join(sorted(anchor_cuisines))}（可在下方菜系筛选中按需聚焦）")

    avail_only = st.toggle("🥕 仅显示可做的晚餐（根据现有库存）", key="picker_avail_only")
    if avail_only:
        inv = get_all_inventory()
        available = _build_avail_set(inv)
        main_ings = _fetch_all_main_ings()
        pool = [
            r for r in all_recipes
            if _can_make(r["id"], main_ings, available)
            and ("主食" in r.get("category", []) or "菜肴" in r.get("category", []))
        ]
        st.caption(f"晚餐匹配：**{len(pool)}** 道可做")
    else:
        pool = all_recipes

    # Available cuisines (from current pool)
    cuisine_opts = sorted({r.get("cuisine") for r in pool if r.get("cuisine")})

    fc1, fc2, fc3, fc4, fc5 = st.columns([3, 1.6, 1.6, 1.6, 1.6])
    f_search = fc1.text_input(
        "搜索", placeholder="🔍 菜名关键字…", label_visibility="collapsed",
    )
    f_meat    = fc2.selectbox("荤素",     ["🥩 荤素 (全部)"] + _CATS_MEAT, label_visibility="collapsed")
    f_method  = fc3.selectbox("烹饪方式", ["🍳 方式 (全部)"] + _METHODS,   label_visibility="collapsed")
    f_form    = fc4.selectbox("种类",     ["🍱 种类 (全部)"] + _CATS_FORM,  label_visibility="collapsed")
    f_cuisine = fc5.selectbox("菜系",     ["🍱 菜系 (全部)"] + cuisine_opts, label_visibility="collapsed")

    filtered = pool
    if f_search.strip():
        filtered = [r for r in filtered if f_search.strip() in r["name"]]
    if f_meat != "🥩 荤素 (全部)":
        filtered = [r for r in filtered if f_meat in r.get("category", [])]
    if f_method != "🍳 方式 (全部)":
        filtered = [r for r in filtered if f_method in r.get("cooking_method", [])]
    if f_form != "🍱 种类 (全部)":
        filtered = [r for r in filtered if f_form in r.get("category", [])]
    if f_cuisine != "🍱 菜系 (全部)":
        filtered = [r for r in filtered if r.get("cuisine") == f_cuisine]

    if not filtered:
        st.caption("暂无匹配菜谱")
        return

    # Wishlist IDs (any in wishlist, regardless of date) → ⭐ marker in picker
    from views.wishlist import get_wishlist
    wishlist_rids = {w["recipe_id"] for w in get_wishlist()}

    h1, h2, h3, h4 = st.columns([4, 2.5, 1, 1])
    h1.caption("菜名")
    h2.caption("分类")
    h3.caption("难度")

    for r in filtered:
        c1, c2, c3, c4 = st.columns([4, 2.5, 1, 1])
        star = "⭐ " if r["id"] in wishlist_rids else ""
        wok  = "🍳 " if r.get("uses_wok")   else ""
        para = "⚡ " if r.get("is_parallel") else ""
        t    = f" {r['cook_time_min']}min" if r.get("cook_time_min") else ""
        c1.write(f"{star}{wok}{para}**{r['name']}**{t}")
        if r.get("category"):
            c2.caption(" · ".join(r["category"]))
        c3.caption(r.get("prep_difficulty", ""))

        already = r["id"] in current
        if already:
            c4.button("✓ 已选", key=f"pick_{r['id']}", disabled=True, use_container_width=True)
        else:
            if c4.button("＋ 加入", key=f"pick_{r['id']}", use_container_width=True):
                st.session_state[_RIDS].append(r["id"])
                st.session_state[_CONFIRM] = False
                st.rerun()


def _section_placeholder() -> None:
    st.caption(
        "临时食材不保存到菜谱库，仅计入今日营养分析。  \n"
        "每行格式：`食材名  数量  单位`  "
        "（例：`猪五花  300  g`  |  `鸡蛋  2  个`  |  `生抽  15  ml`）"
    )
    st.text_area(
        "临时食材",
        key="plan_ph_raw",
        height=120,
        placeholder="猪五花  300  g\n西蓝花  250  g\n生抽  15  ml",
        label_visibility="collapsed",
    )
    bc1, bc2 = st.columns([1, 1])
    if bc1.button("✔ 更新占位菜", use_container_width=True):
        raw    = st.session_state.get("plan_ph_raw", "")
        parsed = _parse_ph(raw)
        st.session_state[_PH]      = parsed
        st.session_state[_CONFIRM] = False
        if parsed:
            st.toast(f"已录入 {len(parsed)} 种临时食材")
        st.rerun()
    if bc2.button("🗑️ 清空", use_container_width=True):
        st.session_state[_PH]           = []
        st.session_state["plan_ph_raw"] = ""
        st.session_state[_CONFIRM]      = False
        st.rerun()


# ── Entry point ───────────────────────────────────────────────

def show() -> None:
    _init()

    st.title("📅 今日食谱规划")
    st.caption(datetime.now().strftime("%Y年%m月%d日 %A"))

    all_recipes   = get_all_recipes()
    recipes_by_id = {r["id"]: r for r in all_recipes}

    rids = st.session_state[_RIDS]
    ph   = st.session_state[_PH]

    _section_recommend()
    st.divider()

    left, right = st.columns([3, 2])
    with left:
        _section_menu(recipes_by_id)
        _section_confirm(recipes_by_id)
    with right:
        st.subheader("📊 营养预估")
        _nutrition_preview(rids, ph, recipes_by_id)

    st.divider()

    # Sticky expand state — compute initial default once, don't re-evaluate each
    # rerun (otherwise adding a recipe flips len(rids)==0 to False and Streamlit
    # force-collapses the picker mid-flow).
    if "picker_expanded" not in st.session_state:
        st.session_state["picker_expanded"] = (len(rids) == 0)

    with st.expander(
        f"🍳 添加菜谱（共 {len(all_recipes)} 道）",
        expanded=st.session_state["picker_expanded"],
    ):
        if not all_recipes:
            st.info("菜谱库为空，请先在「🍳 菜谱库」页面添加菜谱。")
        else:
            _section_picker(all_recipes, recipes_by_id)

    with st.expander("✏️ 临时占位菜（不录库）", expanded=False):
        _section_placeholder()