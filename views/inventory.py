import json
import os
import re

import streamlit as st
from utils.cache import (
    get_all_inventory_cached as get_all_inventory,
    toggle_in_stock, set_quantity, add_item, delete_item,
    toggle_perishable,
)

_INV_CATEGORIES = ["leafy_veg", "protein", "seasoning", "dry_goods", "other"]
_INV_CAT_LABELS = {
    "leafy_veg": "🥬 叶菜/时令",
    "protein":   "🥩 蛋白/冷库",
    "seasoning": "🧂 调味",
    "dry_goods": "🏺 干货",
    "other":     "📦 其他",
}

_QTY_COLS_NORMAL   = [4.5, 1.8]                # [status+name+grams | number_input]
_QTY_COLS_SHOPPING = [3.0, 1.5, 2.2, 1.2]
_BAR_DOTS = 10


def _dot(qty: float) -> str:
    if qty <= 0: return "🔘"
    if qty >= 4: return "🟠"
    return "🟢"


def _portion_bar(qty: float) -> str:
    """Visual fill bar: ●●●○○○○○○○ — caps at _BAR_DOTS, '+' suffix for overflow."""
    filled   = min(int(qty), _BAR_DOTS)
    empty    = max(0, _BAR_DOTS - filled)
    overflow = "+" if qty > _BAR_DOTS else ""
    return "●" * filled + "○" * empty + overflow


def _group_by_status(items: list) -> list:
    """Split items into status-based groups. Returns ordered list of (emoji, label, items)."""
    empty   = [i for i in items if (i.get("quantity") or 0) <= 0]
    perish  = [i for i in items if i.get("is_perishable") and (i.get("quantity") or 0) > 0]
    stocked = [i for i in items if not i.get("is_perishable") and (i.get("quantity") or 0) >= 4]
    normal  = [i for i in items if not i.get("is_perishable") and 0 < (i.get("quantity") or 0) < 4]
    out: list = []
    if perish:  out.append(("🔴", "易坏优先消耗",        perish))
    if stocked: out.append(("🟠", "囤货较多（≥4 份）",   stocked))
    if normal:  out.append(("🟢", "正常库存",            normal))
    if empty:   out.append(("🔘", "已用完",              empty))
    return out


# ─── Boolean item renderer ────────────────────────────────────

def _render_bool_items(items: list) -> None:
    """3-column grid of toggle buttons. Notes shown as hover tooltip."""
    cols = st.columns(3)
    for i, item in enumerate(items):
        in_stock = bool(item.get("in_stock"))
        icon = "🟢" if in_stock else "⬜"
        with cols[i % 3]:
            if st.button(
                f"{icon}  {item['name']}",
                key=f"toggle_{item['id']}",
                help=item.get("notes") or None,
                use_container_width=True,
            ):
                toggle_in_stock(item["id"], not in_stock)
                st.rerun()


# ─── Portion (Unit) item renderer ───────────────────────────

def _render_portion_item(item: dict, shopping_mode: bool, step_qty: int = 1) -> None:
    qty = float(item.get("quantity") or 0)
    badge = "🔴 " if item.get("is_perishable") else ""
    name = f"{badge}{item['name']}"
    dot = _dot(qty)

    if shopping_mode:
        c1, c2, c3, c4 = st.columns(_QTY_COLS_SHOPPING)
        c1.write(name)
        c2.write(f"**{int(qty)} 份**")
        add_qty = c3.number_input(
            "购入份数",
            min_value=0,
            value=step_qty,
            step=step_qty,
            key=f"add_{item['id']}",
            label_visibility="collapsed",
        )
        if c4.button("录入", key=f"save_{item['id']}", type="primary", use_container_width=True):
            if add_qty > 0:
                set_quantity(item["id"], qty + add_qty)
                st.rerun()
    else:
        c1, c2 = st.columns(_QTY_COLS_NORMAL)
        portion_g = float(item.get("portion_weight_g") or 0)
        grams = int(qty * portion_g)
        bar   = _portion_bar(qty)
        # No leading dot — the group header already conveys status
        c1.markdown(
            f"{name}　`{bar}`　**{qty:.0f} 份**　_≈ {grams} g_"
        )

        # The DB value is part of the widget key on purpose. Streamlit drops
        # `value=` once a key exists, so with a fixed key this widget would keep
        # serving whatever it last held in THIS session — and the write-on-diff
        # below would then push that stale number back into the DB, silently
        # reverting a deduction made from 今日规划 in another tab/device (or any
        # out-of-process write). Folding qty into the key mints a new widget
        # whenever the stored value changes, so it always re-inits from the DB.
        new_qty = c2.number_input(
            label=f"qty_{item['id']}",
            min_value=0.0,
            value=qty,
            step=float(step_qty),
            key=f"edit_{item['id']}_{qty}",
            label_visibility="collapsed",
        )
        if new_qty != qty:
            set_quantity(item["id"], new_qty)
            st.rerun()


def _qty_headers(shopping_mode: bool) -> None:
    if shopping_mode:
        c1, c2, c3, c4 = st.columns(_QTY_COLS_SHOPPING)
        c1.caption("名称")
        c2.caption("当前")
        c3.caption("购入份数")
        c4.caption("确认")
    else:
        c1, c2 = st.columns(_QTY_COLS_NORMAL)
        c1.caption("名称 · 份数 · 约克重")
        c2.caption("编辑")

# ... (其余 Add/Delete 表单函数保持不变) ...

def _add_bool_form(category: str) -> None:
    with st.expander("➕ 添加条目"):
        with st.form(key=f"add_bool_{category}"):
            name = st.text_input("名称")
            notes = st.text_input("备注（可选）")
            if st.form_submit_button("添加") and name.strip():
                add_item(name.strip(), category, "boolean", notes=notes or None)
                st.rerun()

def _add_mixed_form(category: str) -> None:
    with st.expander("➕ 添加条目"):
        with st.form(key=f"add_mixed_{category}"):
            name = st.text_input("名称")
            is_staple = st.checkbox("🛒 标记为「常备免记量食材」")
            col1, col2 = st.columns(2)
            is_perishable = col1.checkbox("易坏 🔴")
            is_frozen = col2.checkbox("冷冻")
            init_qty = st.number_input("初始份数", min_value=0, value=0, step=1)
            notes = st.text_input("备注")
            if st.form_submit_button("添加") and name.strip():
                item_type = "boolean" if is_staple else "quantity"
                new_id = add_item(name.strip(), category, item_type, is_perishable=is_perishable, is_frozen=is_frozen, notes=notes or None)
                if item_type == "quantity" and init_qty > 0:
                    set_quantity(new_id, float(init_qty))
                st.rerun()

def _delete_mixed_form(items: list) -> None:
    with st.expander("🗑️ 删除条目"):
        if not items:
            st.caption("暂无条目")
            return
        for item in items:
            c1, c2 = st.columns([6, 1])
            badge = "🔴 " if item.get("is_perishable") else ""
            type_icon = "🛒 " if item.get("item_type") == "boolean" else "📦 "
            label = f"{badge}{type_icon}{item['name']}"
            c1.markdown(label)
            if c2.button("✕", key=f"del_mixed_{item['id']}"):
                delete_item(item["id"])
                st.rerun()

def _delete_bool_form(items: list) -> None:
    with st.expander("🗑️ 删除条目"):
        if not items:
            st.caption("暂无条目")
            return
        for item in items:
            c1, c2 = st.columns([6, 1])
            c1.markdown(item["name"])
            if c2.button("✕", key=f"del_bool_{item['id']}"):
                delete_item(item["id"])
                st.rerun()

# ─── Tab renderers ────────────────────────────────────────────

def _render_portion_groups(items: list, shopping_mode: bool) -> None:
    """Grouped display in normal mode (易坏 / 囤货 / 正常 / 已用完);
    flat list in shopping mode (so you can scan & add in original order)."""
    if not items:
        return
    if shopping_mode:
        _qty_headers(shopping_mode)
        for item in items:
            _render_portion_item(item, shopping_mode)
        return
    _qty_headers(shopping_mode)
    for emoji, label, group in _group_by_status(items):
        st.markdown(f"**{emoji} {label}**  <span style='color:gray; font-size:0.85em'>· {len(group)} 项</span>",
                    unsafe_allow_html=True)
        for item in group:
            _render_portion_item(item, shopping_mode)


def _tab_leafy(leafy_qty: list, leafy_bool: list, shopping_mode: bool) -> None:
    st.subheader("🥬 叶菜 / 时令蔬菜")
    _render_portion_groups(leafy_qty, shopping_mode)
    st.divider()
    st.markdown("##### 🛒 常备免记量区")
    if leafy_bool:
        _render_bool_items(leafy_bool)
    _add_mixed_form("leafy_veg")
    _delete_mixed_form(leafy_qty + leafy_bool)


def _tab_protein(prot_qty: list, prot_bool: list, shopping_mode: bool) -> None:
    st.subheader("🥩 蛋白 / 冷库")
    _render_portion_groups(prot_qty, shopping_mode)
    st.divider()
    st.markdown("##### 🛒 常备免记量区")
    if prot_bool:
        _render_bool_items(prot_bool)
    _add_mixed_form("protein")
    _delete_mixed_form(prot_qty + prot_bool)

def _tab_seasoning(seasoning: list) -> None:
    st.subheader("🧂 调味 / 香料")
    if seasoning:
        _render_bool_items(seasoning)
    _add_bool_form("seasoning")
    _delete_bool_form(seasoning)

def _tab_dry(dry: list) -> None:
    st.subheader("🏺 干货柜")
    if dry:
        _render_bool_items(dry)
    _add_bool_form("dry_goods")
    _delete_bool_form(dry)

def _tab_other(other: list) -> None:
    st.subheader("📦 其他")
    if other:
        _render_bool_items(other)
    _add_bool_form("other")
    _delete_bool_form(other)

# ─── AI bulk inventory entry ──────────────────────────────────

_AI_INV_PROMPT = """你是家庭厨房库存助手。把以下自由文本解析为结构化库存条目。

【用户输入】：
{raw}

━━━━━━━━━━━━━━━━━━━━━━━━━━
【任务】
对每个食材，输出：
- name：清洗后的标准中文名（去掉数量词，保留品类，如"黄瓜"、"羊肉片"、"秋葵"）
- portions：份数（整数或 0.5 步长的浮点数；"两份"=2，"半份"=0.5，"一包"=1）
- category：从 `leafy_veg`（叶菜/时令蔬菜/根茎瓜茄）、`protein`（肉/蛋/水产/豆制品/冷冻预制）、`seasoning`（液体调料/酱料/香料）、`dry_goods`（米面豆/坚果/干货）、`other`（其他）中选一个
- item_type：`quantity`（按份记录，蔬菜/蛋白默认）或 `boolean`（常备免记量，调料/干货默认）
- is_perishable：true（叶菜、生肉、活海鲜易坏）/ false（冷冻、根茎、调料、干货）

【中文数字对照】一=1 两/二=2 三=3 四=4 五=5 六=6 七=7 八=8 九=9 十=10 半=0.5

━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出 JSON】（不含 markdown 包裹）：
{{
  "items": [
    {{"name": "黄瓜", "portions": 3, "category": "leafy_veg", "item_type": "quantity", "is_perishable": true}},
    {{"name": "羊肉", "portions": 2, "category": "protein", "item_type": "quantity", "is_perishable": false}}
  ]
}}
"""


def _ai_parse_inventory(raw: str) -> list:
    """Call Gemini, return list of parsed item dicts."""
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未设置，请在 .env 中配置")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=_AI_INV_PROMPT.format(raw=raw),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    text = re.sub(r"^```(?:json)?\s*", "", resp.text.strip())
    text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    return parsed.get("items", [])


def _ai_inventory_expander() -> None:
    """⚡ AI 批量录入 — paste free-form text, Gemini parses, preview & commit."""
    with st.expander("⚡ AI 批量录入（粘贴自然语言，自动解析入库）", expanded=False):
        msg = st.session_state.pop("ai_inv_msg", None)
        if msg:
            st.success(msg)

        # Step 2: preview & commit
        if "ai_inv_parsed" in st.session_state:
            parsed = st.session_state["ai_inv_parsed"]
            inv    = get_all_inventory()
            # Build name → existing item lookup across all categories
            existing: dict = {}
            for cat_items in inv.values():
                for item in cat_items:
                    existing[item["name"]] = item

            st.caption("✏️ 可调整分类/份数。绿色 = 库存已存在（将累加份数）；蓝色 = 新建条目。")

            for i, item in enumerate(parsed):
                ex = existing.get(item["name"])
                c1, c2, c3, c4 = st.columns([2.5, 1, 1.5, 1])

                # Show existing badge
                tag = "🟢 累加" if ex else "🔵 新建"
                c1.write(f"{tag}  **{item['name']}**")

                # Editable portions
                portions = c2.number_input(
                    "份", min_value=0.0, value=float(item.get("portions", 1)),
                    step=0.5, key=f"aip_qty_{i}", label_visibility="collapsed",
                )

                # Category — locked if existing item, editable if new
                if ex:
                    c3.caption(f"_{_INV_CAT_LABELS.get(ex['category'], ex['category'])}_")
                    cat = ex["category"]
                else:
                    default_cat = item.get("category", "other")
                    cat = c3.selectbox(
                        "分类", _INV_CATEGORIES,
                        index=_INV_CATEGORIES.index(default_cat) if default_cat in _INV_CATEGORIES else 4,
                        format_func=lambda x: _INV_CAT_LABELS[x],
                        key=f"aip_cat_{i}", label_visibility="collapsed",
                    )

                # Perishable toggle (new items only)
                if ex:
                    c4.caption("🔴 易坏" if ex.get("is_perishable") else "—")
                else:
                    c4.checkbox("🔴 易坏", value=bool(item.get("is_perishable")),
                                key=f"aip_per_{i}")

                # Stash resolved values back
                parsed[i]["_resolved_cat"] = cat
                parsed[i]["_resolved_qty"] = portions

            st.divider()
            bc1, bc2 = st.columns(2)
            if bc2.button("✅ 确认入库", type="primary", use_container_width=True):
                added, updated = 0, 0
                for i, item in enumerate(parsed):
                    name      = item["name"]
                    portions  = float(item.get("_resolved_qty") or 0)
                    cat       = item.get("_resolved_cat", "other")
                    item_type = item.get("item_type", "quantity")
                    ex        = existing.get(name)

                    if ex:
                        if ex.get("item_type") == "quantity":
                            cur = float(ex.get("quantity") or 0)
                            set_quantity(ex["id"], cur + portions)
                            # Keep the snapshot in sync: `existing` is built once
                            # before the loop, so if the same name appears twice in
                            # one batch (user typed it twice, or Gemini normalised
                            # two phrasings to one name) the second pass would read
                            # the pre-write value and overwrite instead of add.
                            ex["quantity"] = cur + portions
                        else:
                            toggle_in_stock(ex["id"], True)
                        updated += 1
                    else:
                        is_per = bool(st.session_state.get(f"aip_per_{i}", item.get("is_perishable")))
                        new_id = add_item(
                            name, cat, item_type,
                            is_perishable=is_per,
                            portion_weight_g=(500 if cat == "leafy_veg" else 300 if cat == "protein" else 200),
                        )
                        if item_type == "quantity" and portions > 0:
                            set_quantity(new_id, portions)
                        elif item_type == "boolean":
                            toggle_in_stock(new_id, True)
                        # Register it so a duplicate name later in the same batch
                        # accumulates onto this row instead of creating a second one.
                        existing[name] = {"id": new_id, "item_type": item_type,
                                          "quantity": portions}
                        added += 1

                st.session_state.pop("ai_inv_parsed", None)
                # Carried across the rerun below, which discards anything rendered
                # in this run — otherwise the user never sees the result.
                st.session_state["ai_inv_msg"] = f"✅ 入库完成：新建 {added} 项 · 累加 {updated} 项"
                st.rerun()

            if bc1.button("← 重新输入", use_container_width=True):
                st.session_state.pop("ai_inv_parsed", None)
                st.rerun()
            return

        # Step 1: text input
        raw = st.text_area(
            "粘贴库存文字",
            height=120,
            key="ai_inv_raw",
            placeholder="例如：黄瓜3份 羊肉两份 秋葵2份 半份生菜 一包冻虾仁",
            label_visibility="collapsed",
        )
        if st.button("🤖 AI 解析", type="primary", use_container_width=True,
                     disabled=not raw.strip()):
            with st.spinner("Gemini 正在解析…"):
                try:
                    items = _ai_parse_inventory(raw.strip())
                    if items:
                        st.session_state["ai_inv_parsed"] = items
                    else:
                        st.warning("未解析到任何条目，请检查输入")
                except Exception as e:
                    st.error(f"解析失败：{e}")
            st.rerun()


# ─── Entry point ──────────────────────────────────────────────

def show() -> None:
    st.title("📦 库存管理")
    data = get_all_inventory()
    raw_leafy = data.get("leafy_veg", [])
    # Groups handle priority; only alpha-sort within so each group is readable
    leafy_qty = sorted([x for x in raw_leafy if x.get("item_type") == "quantity"], key=lambda x: x["name"])
    leafy_bool = sorted([x for x in raw_leafy if x.get("item_type") == "boolean"], key=lambda x: (not x.get("in_stock"), x["name"]))
    raw_protein = data.get("protein", [])
    prot_qty = sorted([x for x in raw_protein if x.get("item_type") == "quantity"], key=lambda x: x["name"])
    prot_bool = sorted([x for x in raw_protein if x.get("item_type") == "boolean"], key=lambda x: (not x.get("in_stock"), x["name"]))
    dry = sorted(data.get("dry_goods", []), key=lambda x: (not x.get("in_stock"), x["name"]))
    seasoning = sorted(data.get("seasoning", []), key=lambda x: (not x.get("in_stock"), x["name"]))
    other = sorted(data.get("other", []), key=lambda x: (not x.get("in_stock"), x["name"]))

    leafy_portions = sum(i.get("quantity") or 0 for i in leafy_qty)
    prot_portions = sum(i.get("quantity") or 0 for i in prot_qty)
    leafy_kg, prot_kg = (leafy_portions * 500) / 1000, (prot_portions * 300) / 1000
    leafy_days, prot_days = int(leafy_kg / 1.0), int(prot_kg / 0.6)
    urgent = [i for i in leafy_qty if i.get("is_perishable") and (i.get("quantity") or 0) > 0]
    urgent += [i for i in leafy_bool if i.get("is_perishable") and i.get("in_stock")]
    all_bools = leafy_bool + prot_bool + seasoning + dry + other
    bool_in, bool_tot = sum(1 for i in all_bools if i.get("in_stock")), len(all_bools)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("叶菜/时令", f"{leafy_portions:.0f} 份", delta=f"约 {leafy_days} 天份", delta_color="off")
    c2.metric("蛋白冷库", f"{prot_portions:.0f} 份", delta=f"约 {prot_days} 天份", delta_color="off")
    c3.metric("常备/干货", f"{bool_in} / {bool_tot} 有货")
    c4.metric("🔴 易坏优先", f"{len(urgent)} 种", delta="需优先" if urgent else None, delta_color="inverse")

    _ai_inventory_expander()

    shopping_mode = st.toggle("🛒 购物模式")
    st.divider()
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["🥬 叶菜", "🥩 蛋白", "🧂 调味", "🏺 干货", "📦 其他"])
    with tab1: _tab_leafy(leafy_qty, leafy_bool, shopping_mode)
    with tab2: _tab_protein(prot_qty, prot_bool, shopping_mode)
    with tab3: _tab_seasoning(seasoning)
    with tab4: _tab_dry(dry)
    with tab5: _tab_other(other)