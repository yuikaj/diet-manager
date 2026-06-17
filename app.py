import streamlit as st
from db.init_db import init_database
import views.inventory as pg_inventory
import views.recipes   as pg_recipes
import views.nutrition as pg_nutrition
import views.plan      as pg_plan
import views.shopping  as pg_shopping
import views.wishlist  as pg_wishlist

st.set_page_config(
    page_title="饮食健康管理",
    page_icon="🥦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize DB on every cold start (idempotent)
init_database()

# ── Navigation ────────────────────────────────────────────────
PAGES = {
    "📦 Inventory": "inventory",
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
    st.title("🥦 饮食健康管理")
    st.divider()
    page_label = st.radio("导航", list(PAGES.keys()),
                          key="_nav_radio", label_visibility="collapsed")

page = PAGES[page_label]

# ── Page stubs (filled in Phase 2+) ──────────────────────────
if page == "inventory":
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

    from db.init_db import get_connection, DB_PATH, init_database
    init_database()

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
    col1.metric("Inventory 条目", counts["inventory"])
    col2.metric("餐食预设", counts["meal_presets"])
    col3.metric("用户设置", counts["user_settings"])
    st.caption(f"数据库路径：`{DB_PATH}`")
