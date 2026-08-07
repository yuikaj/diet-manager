"""🌌 食愿之书 — wishlist + 库存联动 + 推荐器软偏好."""
import json
import uuid
from datetime import datetime, date

import streamlit as st

from db.init_db import get_connection
from db.recipes import get_recipe
from utils.cache import (
    get_all_recipes_cached as get_all_recipes,
    get_all_ingredients_grouped_cached as get_all_ingredients_grouped,
    get_all_inventory_cached as get_all_inventory,
)

_SETTINGS_KEY = "wishlist"
_STAGE     = "wish_stage"      # list[str] — recipe IDs picked in the browse list, not yet saved
_STAGE_MSG = "wish_stage_msg"  # (level, text) — survives the post-save st.rerun()


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
        if not w.get("recipe_id"):   # custom (not-yet-in-library) entry — nothing to boost
            continue
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
    """All ingredient names currently in stock — shared with 今日规划 and the
    recommender so the 可做 badge here can't contradict either of them."""
    from utils.inventory_state import available_names
    return available_names(get_all_inventory())


def _missing_ingredients(recipe_id: str, all_ings: dict, avail: set) -> list:
    """Return list of main (non-condiment) ingredient names not in inventory."""
    ings = all_ings.get(recipe_id, [])
    mains = [i["name"] for i in ings if not i.get("is_condiment")]
    return [n for n in mains if n not in avail]


# ─── UI ───────────────────────────────────────────────────────

_CUSTOM_PREFIX = "custom:"  # staged-item marker for a not-yet-in-library dish


def _section_stage(recipes_by_id: dict) -> None:
    staged = st.session_state[_STAGE]

    # Outcome of the previous save click — the st.rerun() there discards anything
    # rendered in that run, so the message has to survive in session state.
    msg = st.session_state.pop(_STAGE_MSG, None)
    if msg:
        level, text = msg
        (st.warning if level == "warning" else st.success)(text)

    st.caption("✏️ 菜谱库里还没有的菜，直接写名字也能先记下来：")
    c1, c2 = st.columns([5, 1])
    custom_name = c1.text_input(
        "想吃的菜名", key="wish_custom_name", label_visibility="collapsed",
        placeholder="比如：提拉米苏（还没研究怎么做）",
    )
    # No `disabled=` here: the first click would land on a still-disabled button
    # (typing only reaches the server via the blur-triggered rerun), so it would
    # be swallowed and the user would have to click twice. Validate in-handler.
    if c2.button("＋ 记下", key="wish_custom_add"):
        name = custom_name.strip()
        if not name:
            st.session_state[_STAGE_MSG] = ("warning", "先写个菜名再点「＋ 记下」吧～")
        elif _CUSTOM_PREFIX + name in staged:
            st.session_state[_STAGE_MSG] = ("warning", f"「{name}」已经在下面的待写入列表里了")
        else:
            st.session_state[_STAGE].append(_CUSTOM_PREFIX + name)
            st.session_state.pop("wish_custom_name", None)
        st.rerun()

    if not staged:
        st.caption("🐾 从下面的菜谱里点「＋」，选好的菜会先聚在这里，写好日期/备注再一起收进书里。")
        return

    st.subheader(f"✨ 待写入（{len(staged)} 道）")
    for i, item in enumerate(list(staged)):
        if item.startswith(_CUSTOM_PREFIX):
            label = f"✏️ {item[len(_CUSTOM_PREFIX):]}"
        else:
            recipe = recipes_by_id.get(item)
            label = f"**{recipe['name']}**" if recipe else f"`{item[:8]}`"
        with st.container(horizontal=True, horizontal_alignment="distribute"):
            st.markdown(label)
            # Key on the position, not the value — two identical strings would
            # otherwise collide and abort the whole page render.
            if st.button("✕", key=f"wish_unstage_{i}"):
                st.session_state[_STAGE].pop(i)
                st.rerun()

    c1, c2 = st.columns([1, 1])
    target_date = c1.date_input(
        "想做日期（可选）",
        value=None,
        key="wish_date",
        help="不填 = 没有具体日期，推荐器会一直软偏好；"
             "填了未来日期 → 到当天才进入推荐",
    )
    notes = c2.text_input(
        "备注（可选）",
        key="wish_notes",
        placeholder="如：朋友推荐 / 周末聚餐 / 用上次买的香料",
    )

    if st.button("⭐ 写入食愿之书", type="primary", use_container_width=True):
        existing = _load()
        existing_rids  = {w["recipe_id"]  for w in existing if w.get("recipe_id")}
        existing_names = {w["custom_name"] for w in existing if w.get("custom_name")}
        added, skipped = 0, []
        for item in staged:
            is_custom = item.startswith(_CUSTOM_PREFIX)
            name = item[len(_CUSTOM_PREFIX):] if is_custom else None
            # Custom wishes dedupe by name — without this they'd stack up silently,
            # since they have no recipe_id to match on.
            if (name in existing_names) if is_custom else (item in existing_rids):
                skipped.append(name or recipes_by_id.get(item, {}).get("name", "?"))
                continue
            if is_custom:
                existing_names.add(name)
            else:
                existing_rids.add(item)
            existing.append({
                "id":          str(uuid.uuid4()),
                "recipe_id":   None if is_custom else item,
                "custom_name": name,
                "notes":       notes.strip() or None,
                "target_date": target_date.isoformat() if target_date else None,
                "added_at":    datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            added += 1
        _save(existing)
        st.session_state[_STAGE] = []
        st.session_state.pop("wish_notes", None)
        skip_msg = f"，{len(skipped)} 道已在书中跳过（{'、'.join(skipped)}）" if skipped else ""
        st.session_state[_STAGE_MSG] = (
            ("success", f"✅ 已写入 {added} 道菜{skip_msg}") if added
            else ("warning", f"这些菜都已经在书中了（{'、'.join(skipped)}）")
        )
        st.rerun()


def _section_browse(all_recipes: list, all_ings: dict, avail: set) -> None:
    staged = set(st.session_state[_STAGE])
    already = {w["recipe_id"] for w in _load() if w.get("recipe_id")}

    only_avail = st.checkbox(
        "🥕 仅显示库存可做的菜（仅菜肴/主食）",
        key="wish_only_avail",
        help="开启后，只列出主料齐全的菜肴和主食；甜点/早餐/饮料等不在其中",
    )
    if only_avail:
        pool = [
            r for r in all_recipes
            if not _missing_ingredients(r["id"], all_ings, avail)
            and ("菜肴" in r.get("category", []) or "主食" in r.get("category", []))
        ]
    else:
        pool = all_recipes

    search = st.text_input("搜索菜名…", key="wish_search", label_visibility="collapsed",
                            placeholder="🔍 搜索菜名…")
    if search.strip():
        pool = [r for r in pool if search.strip() in r["name"]]

    st.caption(f"匹配 **{len(pool)}** 道")
    if not pool:
        st.info("无可选菜谱（先到 📦 库存补食材，或调整筛选/搜索）")
        return

    for r in pool:
        with st.container(horizontal=True, horizontal_alignment="distribute"):
            in_book = r["id"] in already
            st.markdown(f"**{r['name']}**" + ("　:gray[已在书中]" if in_book else ""))
            if in_book or r["id"] in staged:
                # Staging something already in the book would be a silent no-op on
                # save, so surface it here instead of letting the user find out later.
                st.button("✓", key=f"wish_pick_{r['id']}", disabled=True)
            elif st.button("＋", key=f"wish_pick_{r['id']}"):
                st.session_state[_STAGE].append(r["id"])
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
        is_custom = not w.get("recipe_id")
        recipe = None if is_custom else get_recipe(w["recipe_id"])
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3.5, 2, 1.2, 0.7])

            # Recipe display (custom not-yet-in-library dish, or deleted recipe)
            if is_custom:
                c1.markdown(f"✏️ **{w.get('custom_name') or '未命名'}**")
                if w.get("notes"):
                    c1.caption(f"💬 {w['notes']}")
                missing = []
            elif not recipe:
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
            if is_custom:
                c3.caption("✏️ 自定义")
            elif not recipe:
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
    recipe_items = [w for w in items if w.get("recipe_id")]  # custom entries have no ingredients to check

    ready = sum(
        1 for w in recipe_items
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
    for w in recipe_items:
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
    if _STAGE not in st.session_state:
        st.session_state[_STAGE] = []

    st.title("🌌 食愿之书")
    st.caption(
        "翻开它，写下命中注定要做的菜。📅 今日规划的 picker 里 ⭐ 标记，"
        "🎲 推荐器 +5.0 软偏好让它们自然降临；完成 ✅ 确认扣减后从书中自动消去。"
    )

    all_recipes   = get_all_recipes()
    recipes_by_id = {r["id"]: r for r in all_recipes}
    all_ings = get_all_ingredients_grouped()
    avail = _build_avail_set()
    items = _load()

    _summary_metrics(items, all_ings, avail)
    st.divider()

    st.subheader("➕ 录入新愿")
    _section_stage(recipes_by_id)

    with st.expander(
        f"🍳 浏览菜谱（共 {len(all_recipes)} 道）",
        expanded=(len(st.session_state[_STAGE]) == 0),
    ):
        _section_browse(all_recipes, all_ings, avail)

    st.divider()
    _list_view(items, all_ings, avail)
