import base64
import hashlib
from datetime import datetime
from pathlib import Path

import streamlit as st
import config
from db.init_db import init_database
import views.inventory as pg_inventory
import views.recipes   as pg_recipes
import views.nutrition as pg_nutrition
import views.plan      as pg_plan
import views.shopping  as pg_shopping
import views.wishlist  as pg_wishlist
import views.tonight   as pg_tonight

MIMI_DIR = Path(__file__).parent / "assets" / "mimi"


def _mimi_asset(name: str):
    """Path to a 喵喵 photo if present, else None (repo works fine without the
    gitignored personal photos — e.g. a fresh clone)."""
    p = MIMI_DIR / name
    return p if p.exists() else None


def _mimi_img_tag(name: str, css_class: str) -> str:
    p = _mimi_asset(name)
    if not p:
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    ext = p.suffix.lstrip(".") or "jpeg"
    return f'<img class="{css_class}" src="data:image/{ext};base64,{b64}">'


def _mimi_of_the_day() -> str:
    """Deterministic per-day pick from the rotating photo pool — same photo for
    everyone on a given day, changes the next day."""
    pool = sorted(MIMI_DIR.glob("pool_*.jpg"))
    if not pool:
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    idx = int(hashlib.sha256(today.encode()).hexdigest(), 16) % len(pool)
    b64 = base64.b64encode(pool[idx].read_bytes()).decode()
    return f'<img class="mimi-of-the-day" src="data:image/jpeg;base64,{b64}">'


st.set_page_config(
    page_title="喵喵亭",
    page_icon=_mimi_asset("favicon.png") or "🐈",
    layout="wide",
    initial_sidebar_state="auto",  # collapsed on narrow (mobile) viewports, expanded on desktop
)

st.markdown("""
<style>
/* Bigger touch targets on small screens — buttons/inputs default to a fairly
   compact desktop size that's hard to tap accurately on a phone. */
@media (max-width: 640px) {
    button[kind], .stButton > button, .stDownloadButton > button {
        min-height: 2.75rem;
        font-size: 1rem;
    }
    div[data-testid="stRadio"] label {
        min-height: 2.5rem;
        display: flex;
        align-items: center;
    }
    input, textarea, select {
        font-size: 16px !important;  /* prevents iOS Safari auto-zoom on focus */
    }
    .block-container {
        padding-left: 1rem;
        padding-right: 1rem;
        padding-top: 1.5rem;
    }
    /* st.title() defaults to a desktop-sized h1 that wraps mid-word on narrow
       screens (e.g. "今夜のおすすめ" splitting after "お") — shrink it and
       keep CJK/Japanese runs from breaking at an awkward point. Scale h2–h4
       down to match so nested headings (dish names, section labels) stay
       visually smaller than the page title instead of towering over it. */
    h1 { font-size: 1.5rem  !important; line-height: 1.35 !important; }
    h2 { font-size: 1.3rem  !important; line-height: 1.3  !important; }
    h3 { font-size: 1.15rem !important; line-height: 1.3  !important; }
    h4 { font-size: 1.05rem !important; line-height: 1.3  !important; }
    h1, h2, h3, h4 {
        word-break: keep-all;
        overflow-wrap: break-word;
    }
}
/* Never let a wide table/dataframe force the whole page to scroll sideways —
   the offending element scrolls internally instead. */
div[data-testid="stDataFrame"], div[data-testid="stTable"] {
    max-width: 100%;
    overflow-x: auto;
}
.mimi-avatar-wrap {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: content-box;
    width: 56px;
    height: 56px;
    border-radius: 50%;
    border: 2px solid #D97B4F;
    overflow: hidden;
    vertical-align: middle;
}
.mimi-avatar {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    object-fit: cover;
    display: block;
}
.mimi-of-the-day {
    width: 100%;
    max-width: 220px;
    border-radius: 12px;
    object-fit: cover;
    display: block;
    margin: 0.25rem auto 0;
}
</style>
""", unsafe_allow_html=True)


def _check_password():
    # Gate the app when it's exposed via Cloudflare Tunnel (no password set = local dev, skip).
    if not config.APP_ACCESS_PASSWORD:
        return True
    if st.session_state.get("_authenticated"):
        return True

    st.title("🐈 喵喵亭")
    pw = st.text_input("请输入访问密码", type="password")
    if pw:
        if pw == config.APP_ACCESS_PASSWORD:
            st.session_state["_authenticated"] = True
            st.rerun()
        else:
            st.error("密码错误")
    return False


if not _check_password():
    st.stop()


@st.cache_resource(show_spinner=False)
def _init_database_once():
    # Runs schema creation + migrations once per server process instead of
    # on every rerun (every button click / widget change triggers a rerun).
    init_database()
    return True


_init_database_once()

# ── Navigation ────────────────────────────────────────────────
PAGES = {
    "🍽️ 今夜のおすすめ": "tonight",
    "📦 库存": "inventory",
    "🍳 菜谱库": "recipes",
    "🌌 食愿之书": "wishlist",
    "📋 购物清单": "shopping",
    "📅 今日规划": "plan",
    "📊 营养分析": "nutrition",
    "⚙️ 设置": "settings",
}

# Resolve any pending programmatic navigation BEFORE the radio is created.
if "_nav_pending" in st.session_state:
    target = st.session_state.pop("_nav_pending")
    if target in PAGES:
        st.session_state["_nav_radio"] = target

with st.sidebar:
    avatar_tag = _mimi_img_tag("avatar.jpg", "mimi-avatar")
    if avatar_tag:
        st.markdown(f'<span class="mimi-avatar-wrap">{avatar_tag}</span>'
                    f'&nbsp;&nbsp;<span style="font-size:1.75rem;'
                    f'font-weight:600;vertical-align:middle;">喵喵亭</span>',
                    unsafe_allow_html=True)
    else:
        st.title("🐈 喵喵亭")
    st.divider()
    page_label = st.radio("导航", list(PAGES.keys()),
                          key="_nav_radio", label_visibility="collapsed")

    motd_tag = _mimi_of_the_day()
    if motd_tag:
        st.divider()
        st.caption("今日份喵喵 🐾")
        st.markdown(motd_tag, unsafe_allow_html=True)

page = PAGES[page_label]

# ── Page stubs (filled in Phase 2+) ──────────────────────────
if page == "tonight":
    pg_tonight.show()

elif page == "inventory":
    pg_inventory.show()

elif page == "recipes":
    pg_recipes.show()

elif page == "plan":
    pg_plan.show()

elif page == "shopping":
    pg_shopping.show()

elif page == "wishlist":
    pg_wishlist.show()

elif page == "nutrition":
    pg_nutrition.show()

elif page == "settings":
    st.title("⚙️ 设置")

    from db.init_db import get_connection, DB_PATH

    conn = get_connection()
    counts = {
        "inventory":     conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0],
        "meal_presets":  conn.execute("SELECT COUNT(*) FROM meal_presets").fetchone()[0],
        "user_settings": conn.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0],
    }
    conn.close()

    st.info("调料摄入比例现已改为**每道菜独立设置**，请在菜谱编辑页调整（默认 100%）。")

    st.subheader("数据库状态")
    col1, col2, col3 = st.columns(3)
    col1.metric("库存条目", counts["inventory"])
    col2.metric("餐食预设", counts["meal_presets"])
    col3.metric("用户设置", counts["user_settings"])
    st.caption(f"数据库路径：`{DB_PATH}`")
