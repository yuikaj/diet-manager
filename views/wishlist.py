"""🌌 食愿之书 — wishlist + 库存联动 + 推荐器软偏好."""
import json
import uuid
from datetime import datetime, date

import streamlit as st

from db.init_db import get_connection
from db.recipes import get_all_recipes, get_recipe, get_all_ingredients_grouped
from db.inventory import get_all_inventory

_SETTINGS_KEY = "wishlist"


# ─── Persistence ──────────────────────────────────────────────

def _load() -> list:
    """Return list of {id, recipe_id, notes, target_date, added_at}."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE key=?", (_SETTINGS_KEY,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return []
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return []


def _save(items: list) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (_SETTINGS_KEY, json.dumps(items, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get_wishlist() -> list:
    """Public read API (used by views.plan for ⭐ markers + recommender boost)."""
    return _load()


def get_active_wishlist_recipe_ids() -> set:
    """Recipe IDs that should currently boost in recommender.
    - target_date null  → always boost
    - target_date today or past → boost (overdue / due today)
    - target_date future → skip (don't push yet)
    """
    today = date.today().isoformat()
    out: set = set()
    for w in _load():
        td = w.get("target_date")
        if not td or td <= today:
            out.add(w["recipe_id"])
    return out


def remove_by_recipe_ids(recipe_ids) -> int:
    """Remove all wishlist entries matching any of the given recipe IDs.
    Returns count removed. Used by plan.py auto-cleanup after 确认扣减.
    """
    if not recipe_ids:
        return 0
    rid_set = set(recipe_ids)
    items = _load()
    kept = [w for w in items if w.get("recipe_id") not in rid_set]
    removed = len(items) - len(kept)
    if removed:
        _save(kept)
    return removed


# ─── Inventory availability check ─────────────────────────────

def _build_avail_set() -> set:
    """All ingredient names currently available — matches plan.py's logic
    exactly so the "可做菜" count is consistent across pages.

    Note: leafy_veg/protein tabs also contain a 常备免记量区 (boolean type
    with in_stock flag), so we accept EITHER quantity > 0 OR in_stock.
    """
    inv = get_all_inventory()
    avail: set = set()
    for cat in ("leafy_veg", "protein"):
        for item in inv.get(cat, []):
            if (item.get("quantity") or 0) > 0 or item.get("in_stock"):
                avail.add(item["name"])
    for cat in ("dry_goods", "seasoning", "other"):
        for item in inv.get(cat, []):
            if item.get("in_stock"):
                avail.add(item["name"])
    return avail


def _missing_ingredients(recipe_id: str, all_ings: dict, avail: set) -> list:
    """Return list of main (non-condiment) ingredient names not in inventory."""
    ings = all_ings.get(recipe_id, [])
    mains = [i["name"] for i in ings if not i.get("is_condiment")]
    return [n for n in mains if n not in avail]


# ─── UI ───────────────────────────────────────────────────────

def _add_form(all_recipes: list, all_ings: dict, avail: set) -> None:
    st.subheader("➕ 录入新愿")

    # Build pool, optionally filtered by "only available"
    only_avail = st.checkbox(
        "🥕 仅显示库存可做的菜",
        key="wish_only_avail",
        help="开启后，主料不齐全的菜不会出现在下拉选项里",
    )
    if only_avail:
        pool = [
            r for r in all_recipes
            if not _missing_ingredients(r["id"], all_ings, avail)
            and ("菜肴" in r.get("category", []) or "主食" in r.get("category", []))
        ]
        st.caption(f"匹配 **{len(pool)}** 道可做菜")
    else:
        pool = all_recipes

    if not pool:
        st.info("无可选菜谱（先到 📦 库存补食材，或关闭过滤）")
        return

    name_to_id = {r["name"]: r["id"] for r in pool}
    c1, c2 = st.columns([3, 1])
    sel_names = c1.multiselect(
        "选菜（可多选）",
        options=list(name_to_id.keys()),
        key="wish_select",
        placeholder="搜索菜名…",
    )
    target_date = c2.date_input(
        "想做日期（可选）",
        value=None,
        key="wish_date",
        help="不填 = 没有具体日期，推荐器会一直软偏好；"
             "填了未来日期 → 到当天才进入推荐",
    )
    notes = st.text_input(
        "备注（可选）",
        key="wish_notes",
        placeholder="如：朋友推荐 / 周末聚餐 / 用上次买的香料",
    )

    if st.button("⭐ 写入食愿之书", type="primary", use_container_width=True,
                 disabled=not sel_names):
        existing = _load()
        existing_rids = {w["recipe_id"] for w in existing}
        added = 0
        for nm in sel_names:
            rid = name_to_id[nm]
            if rid in existing_rids:
                continue
            existing.append({
                "id":          str(uuid.uuid4()),
                "recipe_id":   rid,
                "notes":       notes.strip() or None,
                "target_date": target_date.isoformat() if target_date else None,
                "added_at":    datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            added += 1
        _save(existing)
        # Clear add-form state
        st.session_state.pop("wish_select", None)
        st.session_state.pop("wish_notes", None)
        if added:
            st.success(f"✅ 已写入 {added} 道菜（已在书中的会跳过）")
        else:
            st.warning("这些菜都已经在书中了")
        st.rerun()


def _list_view(items: list, all_ings: dict, avail: set) -> None:
    if not items:
        st.info("书页空白。先在上方写下几道心愿吧。")
        return

    today_iso = date.today().isoformat()

    # Sort: overdue/today first, then no-date, then future date (chronological)
    def _sort_key(w):
        td = w.get("target_date")
        if not td:
            return (1, "")          # no date → middle
        if td <= today_iso:
            return (0, td)          # overdue/today → top
        return (2, td)              # future → bottom

    items_sorted = sorted(items, key=_sort_key)

    st.subheader(f"📖 卷宗（{len(items_sorted)} 道）")

    for w in items_sorted:
        recipe = get_recipe(w["recipe_id"])
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3.5, 2, 1.2, 0.7])

            # Recipe display (handle deleted recipes)
            if not recipe:
                c1.markdown(f"~~_菜谱已被删除_~~  `{w['recipe_id'][:8]}`")
                missing = []
            else:
                missing = _missing_ingredients(w["recipe_id"], all_ings, avail)
                star = "⭐"
                c1.markdown(f"{star} **{recipe['name']}**")
                if w.get("notes"):
                    c1.caption(f"💬 {w['notes']}")

            # Date + age
            td = w.get("target_date")
            if td:
                if td < today_iso:
                    c2.markdown(f"📅 :red[~~{td}~~]  _已过_")
                elif td == today_iso:
                    c2.markdown(f"📅 :orange[**今天**]")
                else:
                    c2.markdown(f"📅 {td}")
            else:
                c2.caption("📅 _无日期_")

            # Availability badge
            if not recipe:
                c3.caption("—")
            elif not missing:
                c3.success("🟢 可做")
            else:
                c3.error(f"🔴 缺 {len(missing)} 项")

            # Action: remove
            if c4.button("✕", key=f"wish_del_{w['id']}", help="从清单移除"):
                _save([x for x in _load() if x["id"] != w["id"]])
                st.rerun()

            # Show missing list (collapsible)
            if missing:
                st.caption(f"   缺料：{'、'.join(missing)}")


def _summary_metrics(items: list, all_ings: dict, avail: set) -> None:
    if not items:
        return
    today_iso = date.today().isoformat()

    ready = sum(
        1 for w in items
        if not _missing_ingredients(w["recipe_id"], all_ings, avail)
    )
    overdue = sum(
        1 for w in items
        if (td := w.get("target_date")) and td < today_iso
    )
    due_today = sum(
        1 for w in items
        if w.get("target_date") == today_iso
    )

    missing_all: set = set()
    for w in items:
        for n in _missing_ingredients(w["recipe_id"], all_ings, avail):
            missing_all.add(n)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总计", f"{len(items)} 道")
    c2.metric("🟢 现在可做", f"{ready} 道")
    c3.metric("📅 今天/过期", f"{due_today + overdue}",
              delta=f"含 {overdue} 道过期" if overdue else None,
              delta_color="inverse" if overdue else "off")
    c4.metric("🔴 缺料食材", f"{len(missing_all)} 种",
              help="书中所有未竟之愿累计缺的独特食材数")

    if missing_all:
        with st.expander(f"📋 缺料汇总（{len(missing_all)} 种）"):
            st.caption("把这些加入购物清单，下次采购顺手买齐：")
            st.code("、".join(sorted(missing_all)), language=None)


# ─── Entry point ──────────────────────────────────────────────

def show() -> None:
    st.title("🌌 食愿之书")
    st.caption(
        "翻开它，写下命中注定要做的菜。📅 今日规划的 picker 里 ⭐ 标记，"
        "🎲 推荐器 +5.0 软偏好让它们自然降临；完成 ✅ 确认扣减后从书中自动消去。"
    )

    all_recipes = get_all_recipes()
    all_ings = get_all_ingredients_grouped()
    avail = _build_avail_set()
    items = _load()

    _summary_metrics(items, all_ings, avail)
    st.divider()
    _add_form(all_recipes, all_ings, avail)
    st.divider()
    _list_view(items, all_ings, avail)
