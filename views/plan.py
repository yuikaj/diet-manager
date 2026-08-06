"""Phase 5: 今日食谱规划 — 智能推荐 + 手动选菜 + Placeholder + 实时营养 + 库存扣减"""
import json
import re
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from db.init_db import get_connection
from db.recipes import get_recipe, get_ingredients
# mark_cooked comes from utils.cache (auto-invalidates the recipe read cache).
from utils.cache import (
    get_all_recipes_cached as get_all_recipes, invalidate_recipes_cache,
    get_all_inventory_cached as get_all_inventory, set_quantity, mark_cooked,
)
from utils.nutrition_lookup import calc_nutrition_with_breakdown, to_grams
from utils.recommender import recommend
from utils.pdf_generator import generate_daily_menu_pdf, open_pdf
from views.nutrition import get_pdf_nutrition_dict, get_fruits_list, remember_new_fruits

# ── Session state keys ────────────────────────────────────────
_RIDS    = "plan_rids"     # list[str]  — selected recipe IDs (ordered)
_PH      = "plan_ph"       # list[dict] — placeholder ingredient dicts
_CONFIRM = "plan_confirm"  # bool       — show deduction confirmation panel
_COMBOS  = "plan_combos"   # list[dict] | None — recommender output cache
# Recipe IDs already confirmed+deducted today. Tracked per-dish (not a single
# "menu done" flag) so that adding a dish after confirming can still be deducted,
# while dishes already deducted are never deducted twice. The menu itself is kept
# on screen after confirming so print/publish stay usable.
_DEDUCTED     = "plan_deducted"      # list[str]
_DEDUCTED_DAY = "plan_deducted_day"  # "YYYY-MM-DD" — resets the above on day rollover
_CONFIRM_MSG  = "plan_confirm_msg"   # (level, text) — survives the post-confirm rerun
_PH_DONE      = "plan_ph_done"       # signature of the placeholder set already logged

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

def _cooked_today() -> list:
    """Recipe IDs already cooked today, straight from the DB.

    `plan_deducted` alone lives in session state, which is wiped by a refresh, a
    second tab, or a server restart — and the deduction it guards is a permanent
    DB write. Re-seeding from `last_cooked` (stamped by mark_cooked in the very
    same handler that deducts) makes the guard survive those, so re-picking a
    dish you already cooked today can't silently deduct its ingredients twice.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id FROM recipes WHERE date(last_cooked) = date('now','localtime')"
        ).fetchall()
        conn.close()
        return [r["id"] for r in rows]
    except Exception:
        return []


def _init() -> None:
    first_visit = _RIDS not in st.session_state
    for key, default in [(_RIDS, []), (_PH, []), (_CONFIRM, False), (_COMBOS, None),
                         (_DEDUCTED, []), (_DEDUCTED_DAY, ""), (_PH_DONE, "")]:
        if key not in st.session_state:
            st.session_state[key] = default

    today = datetime.now().strftime("%Y-%m-%d")
    rolled_over = st.session_state[_DEDUCTED_DAY] != today

    if rolled_over:
        # A tab left open overnight still holds yesterday's menu. Clearing only
        # _DEDUCTED would make every one of yesterday's dishes look un-deducted,
        # and one stray click would re-deduct the whole meal *and* overwrite
        # today's nutrition log with yesterday's dinner. Start a clean day instead.
        st.session_state[_DEDUCTED_DAY] = today
        if not first_visit:
            st.session_state[_RIDS]    = []
            st.session_state[_PH]      = []
            st.session_state[_CONFIRM] = False
        st.session_state[_PH_DONE] = ""

    if first_visit or rolled_over:
        st.session_state[_DEDUCTED] = _cooked_today()


def _ph_signature(ph: list) -> str:
    return json.dumps(ph, sort_keys=True, ensure_ascii=False)


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


# Share of the daily protein target that dinner is expected to cover. Matches
# the "晚餐理想约 35–40%" guidance shown on the 营养分析 page.
_DINNER_PROTEIN_SHARE = (0.35, 0.45)


def _protein_target() -> tuple:
    """(min, max) dinner protein for ONE person, as a slice of the daily target.

    This used to be its own body-weight × multiplier calculation, which meant
    今日规划 and 营养分析 scored protein against two unrelated targets — one could
    say 达标 while the other said 不足 for the same meal. Both now derive from the
    macro split configured in ⚙️ 设置.
    """
    try:
        from views.nutrition import _dri
        daily_1p = _dri()["protein"]
    except Exception:
        daily_1p = 106.0
    lo, hi = _DINNER_PROTEIN_SHARE
    return daily_1p * lo, daily_1p * hi


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
        st.caption("🐾 选好菜后，这里会自动算营养账～")
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
        # p_min/p_max are already per-person dinner targets — don't divide again.
        prot_pp = n.protein / pp
        pct = min(prot_pp / p_max, 1.0) if p_max > 0 else 0.0
        dot = "🟡" if prot_pp < p_min else ("🟢" if prot_pp <= p_max else "🔵")
        st.progress(pct, text=f"{dot} 蛋白质/人 {prot_pp:.1f}g　晚餐目标 {p_min:.0f}–{p_max:.0f}g"
                              f"（全日 {p_max/_DINNER_PROTEIN_SHARE[1]:.0f}g 的 35–45%）")

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
            from views.nutrition import ingredient_fix_form
            ingredient_fix_form([r["食材"] for r in bds], "fixplan")


# ── Inventory deduction helpers (Portion logic) ───────────────

def _main_ingredient_names(recipe_ids) -> set:
    names: set = set()
    for rid in recipe_ids:
        for ing in get_ingredients(rid):
            if not ing.get("is_condiment"):
                names.add(ing["name"])
    return names


def _compute_deductions(recipe_ids: list, already_covered: set = frozenset()) -> tuple:
    inv = get_all_inventory()
    item_map: dict = {}
    for cat in ("leafy_veg", "protein", "seasoning", "dry_goods", "other"):
        for item in inv.get(cat, []):
            item_map[item["name"]] = item

    usage_names = _main_ingredient_names(recipe_ids)

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
                    # Ingredients already deducted by an earlier batch today default
                    # to 0 so the total doesn't depend on whether the user confirmed
                    # both dishes at once or one at a time — deductions are keyed by
                    # ingredient (a set), while confirmation is keyed by dish.
                    "covered":     name in already_covered,
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

    current = set(st.session_state[_RIDS])
    cols = st.columns(len(combos))
    for ci, (col, combo) in enumerate(zip(cols, combos)):
        recipes = combo["recipes"]
        stats   = combo["stats"]
        with col:
            with st.container(border=True):
                for i, r in enumerate(recipes):
                    icons = ("🍳 " if r.get("uses_wok") else "") + ("⚡" if r.get("is_parallel") else "")
                    t = f" {r['cook_time_min']}min" if r.get("cook_time_min") else ""
                    # Per-dish add, so a combo can be cherry-picked instead of
                    # taken whole. Key carries the combo index because the same
                    # recipe can legitimately appear in both combos, and two
                    # identical widget keys abort the whole page render.
                    with st.container(horizontal=True, horizontal_alignment="distribute"):
                        st.markdown(f"**{i+1}. {r['name']}** {icons}{t}")
                        if r["id"] in current:
                            st.button("✓", key=f"rec_add_{ci}_{r['id']}", disabled=True,
                                      help="已在今日菜单中")
                        elif st.button("＋", key=f"rec_add_{ci}_{r['id']}",
                                       help="只把这道菜加入今日菜单"):
                            st.session_state[_RIDS].append(r["id"])
                            st.session_state[_CONFIRM] = False
                            st.rerun()
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

                if st.button("✅ 采用此套餐（替换当前菜单）", key=f"adopt_{ci}",
                             use_container_width=True):
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
    st.success(f"🐾 菜单已经印好啦，正在为你打开：`{path}`")


def _print_control_center() -> None:
    """Cross-page state console — same keys as views/nutrition.py.
    Streamlit binds widgets with identical keys to the same session_state slot,
    so edits here flow into the 营养分析 tab automatically and vice versa.
    The restore/remember pair keeps them alive across visits to OTHER pages,
    which would otherwise garbage-collect these widgets' state.
    """
    from views.nutrition import restore_fd_state, remember_fd_state
    restore_fd_state()
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
        fc1.multiselect("今日水果", get_fruits_list(), default=["苹果", "香蕉", "蓝莓"],
                        key="fd_fruits", label_visibility="collapsed",
                        accept_new_options=True,
                        placeholder="选择或直接输入新水果（会被记住）")
        remember_new_fruits(st.session_state.get("fd_fruits"))
        fc2.number_input("每种(g)", min_value=30, max_value=200, value=65, step=5,
                         key="fd_fruit_g")

        # ── Dinner add-ons ───────────────────────────────────
        st.markdown("**➕ 晚餐临时加菜（总克数，会 ÷2 算每人份）**")
        st.text_area("加菜食材", height=70, key="fd_dinner_addons_txt",
                     label_visibility="collapsed",
                     placeholder="莴笋 150 g\n金针菇 100 g")

    remember_fd_state()


def _section_menu(recipes_by_id: dict) -> None:
    rids = st.session_state[_RIDS]
    ph   = st.session_state[_PH]

    h_col, btn_col, pub_col = st.columns([4, 2, 2])
    h_col.subheader("📋 今日菜单")
    if rids:
        if btn_col.button("🖨️ 打印今日菜单", use_container_width=True):
            _print_pdf(recipes_by_id)
        if pub_col.button("📢 发布今日菜单", use_container_width=True,
                          help="发布后，家人打开「🍽️ 今夜のおすすめ」就能看到今天选好的菜和营养摘要"):
            from views.tonight import publish_today_menu
            publish_today_menu(rids, ph)
            st.toast("🐾 已发布，家人现在可以在「🍽️ 今夜のおすすめ」看到今日菜单")

    if rids:
        _print_control_center()

    if not rids and not ph:
        st.info("🐾 菜篮子还空着呢，去下面的菜谱库选几道菜，或者用「临时占位菜」直接写食材吧。")
        return

    selected = [recipes_by_id[r] for r in rids if r in recipes_by_id]

    deducted_now = set(st.session_state[_DEDUCTED])
    for i, r in enumerate(selected):
        c1, c2, c3 = st.columns([4.5, 2.5, 0.8])
        wok  = " 🍳" if r.get("uses_wok")   else ""
        para = " ⚡" if r.get("is_parallel") else ""
        t    = f" ({r['cook_time_min']}min)" if r.get("cook_time_min") else ""
        # Make "already deducted" visible: otherwise a dish that was confirmed
        # earlier today (or removed and re-added) silently has no confirm entry
        # and the user can't tell why.
        done = "　:green[✓ 已扣库存]" if r["id"] in deducted_now else ""
        c1.markdown(f"**{i+1}. {r['name']}**{wok}{para}{t}{done}")
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

    # Skip dishes confirmed earlier today: mark_cooked just stamped them, so they
    # would otherwise flag themselves as "刚做过" the moment you confirm.
    deducted = st.session_state[_DEDUCTED]
    bored = _boredom_violations([r for r in rids if r not in deducted])
    if bored:
        st.warning(f"🔄 防厌倦：「{'、'.join(bored)}」48小时内刚做过，建议换菜")

    uncov = _uncovered_perishables(rids)
    if uncov:
        st.warning(f"🔴 易坏优先：**{'、'.join(uncov)}** 库存充足但今日菜单未消耗，建议加菜")


def _start_new_round(retract_published: bool) -> None:
    cleared = list(st.session_state[_RIDS])
    st.session_state[_RIDS]     = []
    st.session_state[_PH]       = []
    st.session_state[_CONFIRM]  = False
    st.session_state["plan_ph_raw"] = ""   # else 「✔ 更新占位菜」 revives last round's text
    st.session_state[_COMBOS]   = None
    st.session_state[_PH_DONE]  = ""
    # NOTE: _DEDUCTED is deliberately NOT cleared — it records what was already
    # taken out of inventory *today*. Clearing it would let the same dish be
    # deducted a second time by re-picking it.
    for k in [k for k in st.session_state if k.startswith("plan_deduct_")]:
        del st.session_state[k]

    # Only retract the family view if it is showing the menu being cleared.
    # Blanket-clearing would wipe tonight's published menu the moment you start
    # planning tomorrow — while your family is still looking at it.
    if retract_published and cleared:
        try:
            from views.tonight import get_today_menu, clear_today_menu
            published = set(get_today_menu().get("recipe_ids") or [])
            if published and published <= set(cleared):
                clear_today_menu()
        except Exception:
            pass


def _section_confirm(recipes_by_id: dict) -> None:
    rids     = st.session_state[_RIDS]
    ph       = st.session_state[_PH]
    deducted = st.session_state[_DEDUCTED]
    pending  = [r for r in rids if r not in deducted]
    # A placeholder-only menu still needs a confirm entry — it deducts nothing,
    # but it is the only way its ingredients reach the daily nutrition log.
    ph_pending = bool(ph) and st.session_state[_PH_DONE] != _ph_signature(ph)

    if not rids and not ph and not deducted:
        return

    st.divider()

    # Surface the outcome of the previous confirm click (the st.rerun() that
    # follows it discards anything rendered in that run, including error text).
    msg = st.session_state.pop(_CONFIRM_MSG, None)
    if msg:
        level, text = msg
        (st.warning if level == "warning" else st.success)(text)

    if not pending and not ph_pending:
        done_here = [r for r in rids if r in deducted]
        if rids:
            st.success(f"🐾 喵！这 {len(done_here)} 道菜的库存都扣好了。菜单还留在上面，可以继续打印或发布～")
        elif ph:
            st.success("🐾 临时占位菜已记入今日营养。")
        else:
            # Menu emptied after confirming — don't claim a menu is "still up there".
            st.info("🐾 今天已经扣过库存了。菜单已被清空，可以重新选菜（今天做过的菜不会重复扣减）。")
        if st.button("🆕 开始新一轮规划（清空当前菜单）", use_container_width=True):
            _start_new_round(retract_published=True)
            st.rerun()
        return

    if not st.session_state[_CONFIRM]:
        if pending:
            label = ("✅ 确认菜单并扣减库存" if not deducted
                     else f"✅ 新增了 {len(pending)} 道菜，确认并扣减")
        else:
            label = "✅ 确认并记入今日营养"   # placeholder-only: nothing to deduct
        if st.button(label, type="primary", use_container_width=True):
            st.session_state[_CONFIRM] = True
            st.rerun()
        if deducted:
            st.caption(f"已扣减过的 {len(deducted)} 道菜不会重复扣减。")
        return

    # Only the not-yet-deducted dishes participate — re-confirming must never
    # deduct twice for a dish that was already settled earlier today.
    covered = _main_ingredient_names(deducted)
    deductions, used_booleans, unmatched = _compute_deductions(pending, covered)
    names = [recipes_by_id[r]["name"] for r in pending if r in recipes_by_id]

    st.subheader("🗃️ 扣减提议 (按份)")
    head = f"菜单：{' + '.join(names)}" if names else "临时占位菜"
    st.caption(f"{head}\n\n系统默认扣减 1 份，请根据实际消耗量微调：")

    deduct_inputs = {}

    if deductions:
        with st.container(border=True):
            for row in deductions:
                c1, c2, c3 = st.columns([3, 2, 3])
                tag = "　:gray[今天已扣过]" if row.get("covered") else ""
                c1.write(f"**{row['name']}**{tag}")
                c2.write(f"当前: {row['current_qty']:.0f} 份")

                # min(), not a 0 fallback: with `0.0` a leftover 0.5 份 could never
                # be used up by the default flow, so it sat in stock forever while
                # still counting as "有货" for the can-I-cook-this filter.
                default_deduct = 0.0 if row.get("covered") else min(1.0, row['current_qty'])

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
        for rid in pending:
            mark_cooked(rid)
        invalidate_recipes_cache()

        # 4. Auto-remove these recipes from 🌌 食愿之书 (done = wish fulfilled)
        wishlist_removed = 0
        try:
            from views.wishlist import remove_by_recipe_ids
            wishlist_removed = remove_by_recipe_ids(pending)
        except Exception:
            pass

        st.session_state[_CONFIRM] = False
        st.session_state[_DEDUCTED] = deducted + pending
        st.session_state[_PH_DONE]  = _ph_signature(ph)
        wl_msg = f" · 🌌 食愿之书消去 {wishlist_removed} 道" if wishlist_removed else ""
        # Carried across the rerun below, which would otherwise discard it — this
        # is the only channel that reports a failed nutrition save.
        st.session_state[_CONFIRM_MSG] = (
            "warning" if "⚠️" in nutrition_msg else "success",
            f"🐾 库存已经更新好啦{nutrition_msg}{wl_msg}",
        )
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
                        # Same as the other add/remove sites: collapse the open
                        # deduction panel so the new dish can't slip into a
                        # proposal the user already scrolled past.
                        st.session_state[_CONFIRM] = False
                        st.rerun()

    # ── Cuisine auto-anchor hint ──────────────────────────────
    anchor_cuisines = {
        recipes_by_id[rid].get("cuisine")
        for rid in current if recipes_by_id.get(rid, {}).get("cuisine")
    }
    if anchor_cuisines:
        st.caption(f"🍱 已锁定菜系：{'、'.join(sorted(anchor_cuisines))}（可在下方菜系筛选中按需聚焦）")

    avail_only = st.toggle("🥕 仅显示可做的晚餐（根据现有库存）", value=True, key="picker_avail_only")
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
    wishlist_rids = {w["recipe_id"] for w in get_wishlist() if w.get("recipe_id")}

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
            st.info("🐾 菜谱库还空空的，先去「🍳 菜谱库」添加第一道菜吧。")
        else:
            _section_picker(all_recipes, recipes_by_id)

    with st.expander("✏️ 临时占位菜（不录库）", expanded=False):
        _section_placeholder()