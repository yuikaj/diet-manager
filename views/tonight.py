"""🍽️ 今夜のおすすめ — 只读、手机优先的家人共享视图。

读取 views.plan 手动「发布」写入的 today_menu（user_settings JSON blob），
展示今晚菜单 + 每人营养摘要。不提供任何编辑/扣减操作——那些仍在「📅 今日规划」完成。
"""
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from db.init_db import get_connection
from utils.cache import get_all_recipes_cached as get_all_recipes
from utils.nutrition_lookup import calc_nutrition_with_breakdown

_SETTINGS_KEY = "today_menu"
_HERO_IMG = Path(__file__).parent.parent / "assets" / "mimi" / "hero_tonight.jpg"


# ─── Persistence (same user_settings JSON-blob pattern as wishlist/shopping) ──

def _load() -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE key=?", (_SETTINGS_KEY,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return {}


def _save(data: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (_SETTINGS_KEY, json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def _current_extras() -> tuple:
    """Dinner staple + last-minute add-ons from the shared 备餐控制台 widgets.

    Snapshotted at publish time rather than read live, so the family view stays
    consistent with what was actually published even if these are edited later.
    Mirrors compute_fullday_silent()'s parsing so the numbers agree.
    """
    from views.nutrition import _parse_placeholder

    staple_choice = st.session_state.get("fd_staple_choice", "🍚 白米饭")
    staple_ings: list = []
    if staple_choice == "🍚 白米饭":
        staple_g = int(st.session_state.get("fd_staple_g", 100))
        staple_ings = [{"name": "米饭", "amount": float(staple_g), "unit": "g", "intake_ratio": 1.0}]
    elif staple_choice == "✏️ 自定义":
        staple_ings = _parse_placeholder(st.session_state.get("fd_staple_custom_txt", "").strip())

    addon_ings = _parse_placeholder(st.session_state.get("fd_dinner_addons_txt", "").strip())
    return staple_ings, addon_ings


def publish_today_menu(recipe_ids: list, ph: list) -> None:
    """Public write API — called from views.plan's「📢 发布今日菜单」button."""
    try:
        staple_ings, addon_ings = _current_extras()
    except Exception:
        staple_ings, addon_ings = [], []
    _save({
        "date":         datetime.now().strftime("%Y-%m-%d"),
        "recipe_ids":   list(recipe_ids),
        "ph":           list(ph),
        "staple_ings":  staple_ings,
        "addon_ings":   addon_ings,
        "published_at": datetime.now().strftime("%H:%M"),
    })


def clear_today_menu() -> None:
    _save({})


def get_today_menu() -> dict:
    """Returns {} if nothing published, or published for a different day (stale)."""
    data = _load()
    if not data or data.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return {}
    return data


# ─── Display helpers ───────────────────────────────────────────

def _sort_for_menu(recipes: list) -> list:
    def _key(r):
        methods = r.get("cooking_method") or []
        if "凉拌" in methods: return 0
        if "汤"   in methods: return 2
        return 1
    return sorted(recipes, key=_key)


def _build_ings(recipe_ids: list, ph: list, recipes_by_id: dict) -> list:
    """Same conversion as 今日规划 / 全日营养 — the family view must not show a
    different number from the one recorded for the same meal."""
    from views.nutrition import recipe_ings_for_two

    result = []
    for rid in recipe_ids:
        result.extend(recipe_ings_for_two(rid, recipes_by_id.get(rid)))
    result.extend(ph)
    return result


def _dish_card(r: dict) -> None:
    with st.container(border=True):
        icons = ("🍳 " if r.get("uses_wok") else "") + ("⚡ " if r.get("is_parallel") else "")
        st.markdown(f"### {icons}{r['name']}")
        if r.get("en_name"):
            st.caption(r["en_name"])

        desc = (r.get("zh_desc") or r.get("en_desc") or "").strip()
        if desc:
            st.markdown(f"*{desc}*")

        tags = list(r.get("category") or [])
        if r.get("cuisine"):
            tags.append(f"🍱 {r['cuisine']}")
        t = r.get("cook_time_min")
        if t:
            tags.append(f"⏱️ {t}min")
        if tags:
            st.caption("  ·  ".join(tags))


def _nutrition_summary(menu: dict, recipes_by_id: dict) -> None:
    """Per-person dinner total, matching compute_fullday_silent()'s dinner math:
    dishes + placeholders + add-ons are cooked for two and halved; the staple is
    already recorded per-person and is NOT halved.
    """
    shared = _build_ings(menu.get("recipe_ids") or [], menu.get("ph") or [], recipes_by_id)
    shared.extend(menu.get("addon_ings") or [])
    staple = menu.get("staple_ings") or []

    sn, _ = calc_nutrition_with_breakdown(shared)
    pp = 2.0
    kcal, protein, fat, sodium = sn.kcal / pp, sn.protein / pp, sn.fat / pp, sn.sodium / pp
    if staple:
        tn, _ = calc_nutrition_with_breakdown(staple)
        kcal, protein, fat, sodium = (kcal + tn.kcal, protein + tn.protein,
                                      fat + tn.fat, sodium + tn.sodium)

    st.markdown("#### 📊 每人营养摘要")
    c1, c2 = st.columns(2)
    c1.metric("热量",   f"{kcal:.0f} kcal")
    c2.metric("蛋白质", f"{protein:.1f} g")
    c3, c4 = st.columns(2)
    c3.metric("脂肪", f"{fat:.1f} g")
    c4.metric("钠",   f"{sodium:.0f} mg")

    extras = [i["name"] for i in staple] + [i["name"] for i in (menu.get("addon_ings") or [])]
    if extras:
        st.caption("已含：" + "、".join(dict.fromkeys(extras)))

    if sodium > 2300:
        st.error(f"🚨 钠偏高：每人约 {sodium:.0f}mg")


# ─── Entry point ────────────────────────────────────────────────

def show() -> None:
    st.title("🍽️ 今夜のおすすめ")

    menu = get_today_menu()
    if not menu or (not menu.get("recipe_ids") and not menu.get("ph")):
        if _HERO_IMG.exists():
            st.image(str(_HERO_IMG), width='content')
        st.info("🐾 喵～今天还没有发布菜单呢。去「📅 今日规划」选好菜后，点一下「📢 发布今日菜单」，这里就会立刻端上桌～")
        return

    st.caption(f"{datetime.now().strftime('%Y年%m月%d日 %A')}　·　{menu.get('published_at', '')} 发布")

    all_recipes   = get_all_recipes()
    recipes_by_id = {r["id"]: r for r in all_recipes}
    rids = menu.get("recipe_ids") or []
    ph   = menu.get("ph") or []

    selected = _sort_for_menu([recipes_by_id[r] for r in rids if r in recipes_by_id])
    for r in selected:
        _dish_card(r)

    if len(selected) < len(rids):
        st.caption(f"（有 {len(rids) - len(selected)} 道菜谱在发布后被删除了，未能显示）")

    if ph:
        with st.container(border=True):
            st.markdown(f"### ✏️ 临时加菜（{len(ph)} 种食材）")
            st.caption("、".join(p.get("name", "?") for p in ph))

    st.divider()
    _nutrition_summary(menu, recipes_by_id)
