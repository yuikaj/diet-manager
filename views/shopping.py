"""📋 购物清单 — 多店并排录入 + 跨店对比 + AI WeChat 文本解析."""
import json
import os
import re

import streamlit as st

from db.init_db import get_connection

_SETTINGS_KEY = "shopping_list"


# ─── Persistence (user_settings JSON blob) ────────────────────

def _load() -> dict:
    """Load {store_name: textarea_content} dict."""
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


# ─── Item parsing ─────────────────────────────────────────────

_NOTE_SPLIT = re.compile(r"\s{2,}|\t|[,，]\s*")


def _parse_line(line: str) -> tuple:
    """Parse 'name  note' or 'name, note' or just 'name'. Returns (name, note)."""
    line = line.strip().lstrip("•·-* ")
    if not line:
        return ("", "")
    parts = _NOTE_SPLIT.split(line, maxsplit=1)
    name = parts[0].strip()
    note = parts[1].strip() if len(parts) > 1 else ""
    return (name, note)


def _parse_store_items(text: str) -> list:
    """Parse a store's textarea into [(name, note), ...]. Skips blank lines."""
    result = []
    for line in (text or "").splitlines():
        n, note = _parse_line(line)
        if n:
            result.append((n, note))
    return result


# ─── AI WeChat-style parser ───────────────────────────────────

_AI_PROMPT = """你是购物清单整理助手。把以下自由文本拆分为「店名 → 食材列表」。

【用户输入】
{raw}

━━━━━━━━━━━━━━━━━━━━━━━━━━
【识别规则】
- 用户可能用「店名:」「店名：」「店名 -」等多种分隔
- 同一店的项目可能跨多行，或同行用「，」「,」「、」分隔
- 食材名后可能跟备注（价格、品牌、数量等），保留为 note
- 店名通常是英文/拼音/中文短词，如 hmart, costco, 99 Ranch, Whole Foods, T&T

【输出 JSON】（不含 markdown 包裹）：
{{
  "stores": [
    {{
      "name": "店名（规范化大小写，如 Hmart / Costco）",
      "items": [
        {{"name": "食材名", "note": "备注（无则空字符串）"}}
      ]
    }}
  ]
}}
"""


def _ai_parse_wechat(raw: str) -> list:
    """Returns list of {name, items: [{name, note}]} dicts."""
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未设置")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=_AI_PROMPT.format(raw=raw),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    text = re.sub(r"^```(?:json)?\s*", "", resp.text.strip())
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text).get("stores", [])


# ─── Inventory integration ────────────────────────────────────

def _commit_to_inventory(items: list) -> tuple:
    """Add bought items to inventory.

    items: list of (name, portions) tuples (portions > 0).
    Existing items in inventory → increment quantity / set in_stock.
    Unknown items → batch-classify via inventory's _ai_parse_inventory.

    Returns (success: bool, message: str).
    """
    from utils.cache import (
        get_all_inventory_cached as get_all_inventory,
        set_quantity, toggle_in_stock, add_item,
    )

    try:
        inv = get_all_inventory()
    except Exception as e:
        return False, f"读取库存失败：{e}"

    existing: dict = {}
    for cat_items in inv.values():
        for it in cat_items:
            existing[it["name"]] = it

    direct: list = []     # (item_dict, portions)
    new_text_parts: list = []   # for batch AI classification
    new_portion_map: dict = {}  # name → portions
    for name, portions in items:
        if name in existing:
            direct.append((existing[name], portions))
        else:
            new_text_parts.append(f"{name} {portions}份")
            new_portion_map[name] = portions

    # ── Direct updates (existing items, no AI cost) ───────
    for it, portions in direct:
        if it.get("item_type") == "quantity":
            cur = float(it.get("quantity") or 0)
            set_quantity(it["id"], cur + portions)
        else:
            toggle_in_stock(it["id"], True)

    # ── New items: classify via AI batch ──────────────────
    ai_added = 0
    if new_text_parts:
        try:
            from views.inventory import _ai_parse_inventory
            parsed = _ai_parse_inventory(" ".join(new_text_parts))
        except Exception as e:
            return False, (f"已累加 {len(direct)} 项，但 {len(new_text_parts)} 项新条目"
                           f"分类失败：{e}")
        from views.inventory import _INV_CATEGORIES, default_portion_g

        for p in parsed:
            name = p.get("name", "").strip()
            if not name:
                continue
            # Whitelist the model's answer: an unrecognised category writes a row
            # that no inventory tab renders and no availability check sees — the
            # item is in the DB but invisible everywhere.
            cat = p.get("category", "other")
            if cat not in _INV_CATEGORIES:
                cat = "other"
            item_type = p.get("item_type", "quantity")
            portions = float(new_portion_map.get(name, p.get("portions", 1)))
            try:
                new_id = add_item(
                    name, cat, item_type,
                    is_perishable=bool(p.get("is_perishable")),
                    portion_weight_g=default_portion_g(cat),
                )
                if item_type == "quantity" and portions > 0:
                    set_quantity(new_id, portions)
                elif item_type == "boolean":
                    toggle_in_stock(new_id, True)
                ai_added += 1
            except Exception:
                continue

    msg = f"累加 {len(direct)} 项"
    if ai_added:
        msg += f" · 新建 {ai_added} 项"
    return True, msg


# ─── UI helpers ───────────────────────────────────────────────

def _store_columns(stores: list, data: dict) -> dict:
    """Render textareas in rows of up to 3 columns. Returns updated data dict.
    Uses per-store version counter on the widget key so we can force reset
    (st.text_area's internal state otherwise survives pop()/value= changes).
    """
    PER_ROW = 3
    new_data = dict(data)
    for i in range(0, len(stores), PER_ROW):
        row = stores[i:i + PER_ROW]
        cols = st.columns(len(row))
        for col, store in zip(cols, row):
            with col:
                hc1, hc2 = st.columns([5, 1])
                hc1.markdown(f"**🛒 {store}**")
                if hc2.button("✕", key=f"del_store_{store}",
                              help=f"删除「{store}」清单"):
                    new_data.pop(store, None)
                    _save(new_data)
                    st.rerun()
                ver = st.session_state.get(f"shop_ver_{store}", 0)
                current = data.get(store, "")
                new = st.text_area(
                    f"{store}_text",
                    value=current,
                    height=200,
                    key=f"shop_text_{store}__v{ver}",
                    placeholder="一行一项（备注用逗号或双空格分隔）\n冷饮, $8\n苹果  红富士\n三文鱼",
                    label_visibility="collapsed",
                )
                if new != current:
                    new_data[store] = new
    return new_data


def _bump_store_version(store: str) -> None:
    """Force the store's textarea to re-init from value= on next render."""
    st.session_state[f"shop_ver_{store}"] = st.session_state.get(f"shop_ver_{store}", 0) + 1


def _cross_store_summary(data: dict) -> None:
    """Show overlap + coverage analysis."""
    all_items: dict = {}   # name → [(store, note)]
    per_store: dict = {}   # store → {names}
    for store, text in data.items():
        items = _parse_store_items(text)
        per_store[store] = {n for n, _ in items}
        for n, note in items:
            all_items.setdefault(n, []).append((store, note))

    if not all_items:
        st.caption("尚未填入任何项目")
        return

    total = len(all_items)
    overlap = {n: s for n, s in all_items.items() if len(s) > 1}
    coverage = sorted(((s, len(names)) for s, names in per_store.items()),
                      key=lambda x: -x[1])

    c1, c2 = st.columns([1, 2])
    c1.metric("总需求", f"{total} 项")
    if coverage:
        top_store, top_n = coverage[0]
        c2.metric(f"📍 覆盖最多：{top_store}", f"{top_n} / {total} 项",
                  delta=f"+{top_n} 节省时间" if top_n >= total * 0.7 else None,
                  delta_color="off")

    if overlap:
        with st.container(border=True):
            st.markdown("**🔄 在多家店都列入：**")
            for name, occurrences in overlap.items():
                tag_parts = []
                for store, note in occurrences:
                    tag = f"`{store}`"
                    if note:
                        tag += f" _{note}_"
                    tag_parts.append(tag)
                st.caption(f"• **{name}** → {'  ·  '.join(tag_parts)}")


# ─── Entry point ──────────────────────────────────────────────

def show() -> None:
    st.title("📋 购物清单")
    st.caption("按超市分别记录，支持跨店对比。数据自动持久化到 user_settings。")

    data = _load()
    stores = list(data.keys())

    # ── Add new store ─────────────────────────────────────
    ac1, ac2, ac3 = st.columns([3, 1, 1])
    new_store = ac1.text_input(
        "新店名",
        placeholder="如 Hmart / Costco / 99 Ranch / Whole Foods",
        key="shop_new_store",
        label_visibility="collapsed",
    )
    if ac2.button("➕ 添加", use_container_width=True, disabled=not new_store.strip()):
        name = new_store.strip()
        if name not in data:
            data[name] = ""
            _save(data)
            st.session_state["shop_new_store"] = ""
            st.rerun()
        else:
            st.warning(f"「{name}」已存在")
    if ac3.button("🗑️ 清空全部", use_container_width=True,
                  help="删除所有店和清单内容（不可撤销）"):
        if st.session_state.get("_confirm_clear_all"):
            _save({})
            st.session_state.pop("_confirm_clear_all", None)
            st.rerun()
        else:
            st.session_state["_confirm_clear_all"] = True
            st.warning("⚠️ 再点一次确认清空")

    # ── AI parser ─────────────────────────────────────────
    with st.expander("⚡ AI 解析 WeChat 文本（粘贴自由文本，自动拆店）", expanded=False):
        if err := st.session_state.get("shop_ai_error"):
            st.error(err)
            if st.button("✕ 关闭", key="shop_ai_err_dismiss"):
                st.session_state.pop("shop_ai_error", None)
                st.rerun()
        if "shop_ai_parsed" in st.session_state:
            parsed = st.session_state["shop_ai_parsed"]
            st.caption("AI 解析结果，确认后**累加**到现有清单（不会覆盖已有项）")
            for s in parsed:
                items_str = "、".join(
                    f"{i['name']}" + (f" ({i['note']})" if i.get("note") else "")
                    for i in s.get("items", [])
                )
                st.markdown(f"**🛒 {s['name']}**  ·  _{len(s.get('items', []))} 项_")
                st.caption(items_str)
            bc1, bc2 = st.columns(2)
            if bc2.button("✅ 累加入清单", type="primary", use_container_width=True):
                for s in parsed:
                    store = s["name"]
                    existing = data.get(store, "").rstrip()
                    new_lines = []
                    for i in s.get("items", []):
                        line = i["name"]
                        if i.get("note"):
                            line += f"  {i['note']}"
                        new_lines.append(line)
                    if new_lines:
                        prefix = (existing + "\n") if existing else ""
                        data[store] = prefix + "\n".join(new_lines)
                _save(data)
                st.session_state.pop("shop_ai_parsed", None)
                # Force textarea re-init from new data (version bump)
                for s in parsed:
                    _bump_store_version(s["name"])
                st.success("✅ 已合并到清单")
                st.rerun()
            if bc1.button("← 取消", use_container_width=True):
                st.session_state.pop("shop_ai_parsed", None)
                st.rerun()
        else:
            raw = st.text_area(
                "粘贴 WeChat 风格文本",
                height=120,
                key="shop_ai_raw",
                placeholder=(
                    "hmart: 西瓜, 黄瓜, 冷饮 $8, 苹果\n"
                    "costco: 大米, 冷冻虾 $15\n"
                    "whole foods: 三文鱼"
                ),
                label_visibility="collapsed",
            )
            if st.button("🤖 AI 解析", type="primary",
                         disabled=not raw.strip(), use_container_width=True):
                st.session_state.pop("shop_ai_error", None)
                with st.spinner("Gemini 解析中…"):
                    try:
                        parsed = _ai_parse_wechat(raw.strip())
                        if parsed:
                            st.session_state["shop_ai_parsed"] = parsed
                        else:
                            st.session_state["shop_ai_error"] = "未解析到任何店和食材"
                    except Exception as e:
                        st.session_state["shop_ai_error"] = f"解析失败：{e}"
                st.rerun()

    # ── Store textareas ───────────────────────────────────
    st.divider()
    if not stores:
        st.info("还没有添加任何超市。在上方输入框新建，或用「⚡ AI 解析」一次性导入。")
        return

    updated = _store_columns(stores, data)
    # Auto-save on textarea change (no explicit save button needed)
    if updated != data:
        _save(updated)
        data = updated

    # ── Cross-store summary ──────────────────────────────
    st.divider()
    st.subheader("📊 跨店分析")
    _cross_store_summary(data)

    # ── Checkout: per-item portions → inventory ─────────
    st.divider()
    with st.expander("📦 采购完成 → 入库", expanded=False):
        if not stores:
            st.caption("先添加店和清单")
        else:
            sel = st.selectbox(
                "我从哪家店采购完毕：",
                stores, key="shop_done_select",
            )
            items = _parse_store_items(data.get(sel, ""))
            if not items:
                st.caption(f"「{sel}」清单为空")
            else:
                st.caption(
                    "调整每项实际购买份数（0 = 没买/跳过）。"
                    "已在库存的会累加份数；新条目会让 AI 自动分类（叶菜/蛋白/调味/干货/其他）。"
                )

                # Show items with per-item portion input
                buys: dict = {}
                for i, (name, note) in enumerate(items):
                    c1, c2 = st.columns([4, 1])
                    tag = f"  _{note}_" if note else ""
                    c1.markdown(f"**{name}**{tag}")
                    buys[name] = c2.number_input(
                        "份数",
                        min_value=0, max_value=20, value=1, step=1,
                        key=f"shop_buy_{sel}_{i}",
                        label_visibility="collapsed",
                    )

                # Show inline error if commit failed
                if err := st.session_state.get(f"shop_buy_err_{sel}"):
                    st.error(err)

                if st.button(f"📦 入库并清空「{sel}」清单",
                             type="primary", use_container_width=True):
                    to_commit = [(n, q) for n, q in buys.items() if q > 0]
                    if not to_commit:
                        st.warning("所有项都填 0，没有可入库的内容")
                    else:
                        with st.spinner(f"入库 {len(to_commit)} 项中…"):
                            ok, msg = _commit_to_inventory(to_commit)
                        if ok:
                            data[sel] = ""
                            _save(data)
                            _bump_store_version(sel)
                            for k in list(st.session_state.keys()):
                                if k.startswith(f"shop_buy_{sel}_"):
                                    del st.session_state[k]
                            st.session_state.pop(f"shop_buy_err_{sel}", None)
                            st.success(f"✅ {msg} · 「{sel}」清单已清空")
                            st.rerun()
                        else:
                            st.session_state[f"shop_buy_err_{sel}"] = msg
                            st.rerun()
