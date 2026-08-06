"""
Initialize SQLite database: create all tables and insert seed data.
Safe to run multiple times (idempotent via IF NOT EXISTS / INSERT OR IGNORE).
"""
import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "diet.db"


# ─────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────

SCHEMA = """
-- ── Recipes ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recipes (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    source_url      TEXT,
    cooking_method  TEXT NOT NULL DEFAULT '[]',   -- JSON array: ["炒","蒸"]
    uses_wok        INTEGER NOT NULL DEFAULT 0,   -- 1=true
    prep_difficulty TEXT NOT NULL DEFAULT '中等', -- 简单/中等/繁琐
    cook_time_min   INTEGER,
    is_parallel     INTEGER NOT NULL DEFAULT 0,   -- 1=可与其他菜同时进行
    category        TEXT NOT NULL DEFAULT '[]',   -- JSON array: ["荤菜","汤"]（荤素维度+形态维度）
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    data_quality    TEXT NOT NULL DEFAULT 'needs_review', -- complete/needs_review/estimated
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_cooked     TEXT
);

-- ── Ingredients (many-to-one with recipes) ───────────────────
CREATE TABLE IF NOT EXISTS ingredients (
    id              TEXT PRIMARY KEY,
    recipe_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    amount          REAL NOT NULL,
    unit            TEXT NOT NULL DEFAULT 'g',
    is_condiment    INTEGER NOT NULL DEFAULT 0,  -- 1=调料
    intake_ratio    REAL NOT NULL DEFAULT 1.0,   -- 调料实际摄入比例 1.0/0.75/0.5/0.25/0.0
    usda_food_id    TEXT,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);

-- ── Inventory ─────────────────────────────────────────────────
-- category: leafy_veg | protein | dry_goods | seasoning | other
-- item_type: boolean | quantity
CREATE TABLE IF NOT EXISTS inventory (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    item_type       TEXT NOT NULL,    -- boolean / quantity
    in_stock        INTEGER,          -- boolean type: 1=有 0=缺
    quantity        REAL,             -- quantity type: 份数
    unit            TEXT DEFAULT '份',
    is_perishable   INTEGER DEFAULT 0,  -- 1=易坏，推荐优先消耗
    is_frozen       INTEGER DEFAULT 0,
    portion_weight_g REAL DEFAULT 200,  -- protein 每份克重
    notes           TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ── Prepared foods (Module 7: custom nutrition) ───────────────
CREATE TABLE IF NOT EXISTS prepared_foods (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    brand           TEXT,
    serving_weight  REAL,
    custom_kcal     REAL,
    custom_protein  REAL,
    custom_fat      REAL,
    custom_carbs    REAL,
    custom_sodium   REAL,
    custom_fiber    REAL,
    note            TEXT,
    data_source     TEXT NOT NULL DEFAULT 'user_custom',
    inventory_count INTEGER NOT NULL DEFAULT 0,
    is_frozen       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ── Nutrition cache (USDA query results, permanent) ───────────
CREATE TABLE IF NOT EXISTS nutrition_cache (
    usda_food_id    TEXT PRIMARY KEY,
    ingredient_name TEXT NOT NULL,  -- Chinese name key
    en_name         TEXT,
    kcal_per_100g   REAL,
    protein_per_100g REAL,
    fat_per_100g    REAL,
    carbs_per_100g  REAL,
    sodium_per_100g REAL,
    fiber_per_100g  REAL,
    vitc_per_100g   REAL,
    iron_per_100g   REAL,
    calcium_per_100g REAL,
    potassium_per_100g REAL,
    source          TEXT NOT NULL DEFAULT 'usda',  -- usda / local / user_custom
    cached_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ── Meal presets (default breakfast / lunch templates) ────────
CREATE TABLE IF NOT EXISTS meal_presets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    meal_type       TEXT NOT NULL,   -- breakfast / lunch / dinner / snack
    items           TEXT NOT NULL,   -- JSON: structured items or custom nutrition facts
    is_default      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ── Daily logs ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_logs (
    id                      TEXT PRIMARY KEY,
    date                    TEXT NOT NULL UNIQUE,  -- YYYY-MM-DD
    breakfast               TEXT,   -- JSON: {preset_id} or {custom: {kcal,protein,...}}
    lunch                   TEXT,   -- JSON: same format
    dinner_recipe_ids       TEXT DEFAULT '[]',  -- JSON array of recipe IDs
    dinner_prepared_ids     TEXT DEFAULT '[]',  -- JSON array of prepared_food IDs
    dinner_placeholder      TEXT,   -- JSON: ad-hoc ingredient list (Module 3 Placeholder)
    total_kcal              REAL,
    total_protein           REAL,
    total_fat               REAL,
    total_carbs             REAL,
    total_sodium            REAL,
    total_fiber             REAL,
    ai_advice_count         INTEGER NOT NULL DEFAULT 0,  -- max 3/day (Gemini limit)
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ── User settings (key-value store) ──────────────────────────
CREATE TABLE IF NOT EXISTS user_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


# ─────────────────────────────────────────────────────────────
# Seed data helpers
# ─────────────────────────────────────────────────────────────

def _uid():
    return str(uuid.uuid4())


def _inventory_seeds():
    """Return list of (id, name, category, item_type, in_stock, quantity, unit,
    is_perishable, is_frozen, portion_weight_g, notes) tuples."""
    rows = []

    def add(name, category, item_type, *, perishable=0, frozen=0, portion_g=200, notes=None):
        in_stock = 0 if item_type == "boolean" else None
        quantity = 0.0 if item_type == "quantity" else None
        unit = "g" if item_type == "quantity" else None
        rows.append((_uid(), name, category, item_type,
                     in_stock, quantity, unit, perishable, frozen, portion_g, notes))

    # ── 其他常备食材 (other, boolean) ────────────────────────
    for name in ["洋葱", "胡萝卜", "白菜", "番茄", "卷心菜",
                 "土豆", "山药", "藕", "冬瓜", "芋艿"]:
        add(name, "other", "boolean")

    # ── 叶菜/时令 (leafy_veg, quantity) ──────────────────────
    perishable_set = {"豆苗", "黄瓜", "西洋菜", "鸡毛菜", "生菜", "韭菜"}
    for name in ["葱", "豆苗", "花菜", "黄瓜", "芥兰", "金针菇", "萝卜",
                 "茄子", "青菜", "青彩椒", "秋葵", "生菜", "丝瓜", "四季豆",
                 "塔库菜", "茼蒿", "莴笋", "西葫芦", "西蓝花", "西洋菜", "西芹",
                 "杏鲍菇", "绣球菌", "油菜", "韭菜", "鸡毛菜"]:
        add(name, "leafy_veg", "quantity", perishable=(1 if name in perishable_set else 0))

    # ── Protein 冷冻库 (protein, quantity, all frozen) ────────
    proteins = [
        # 禽类
        ("鸡腿", 200), ("鸡翅", 200), ("整鸡", 500), ("鹌鹑", 150), ("整鸭", 600),
        # 牛肉类
        ("肥牛卷", 200), ("牛腩", 200), ("牛腱", 200), ("炒牛肉", 200), ("牛排", 250),
        ("牛小排", 250), ("黄喉", 150), ("牛筋", 200), ("牛尾", 300),
        # 猪肉类
        ("猪小排", 250), ("猪大排", 200), ("猪肉末", 200), ("猪梅肉", 200),
        ("猪肉丝", 200), ("猪五花", 200), ("五花碎", 200),
        # 羊肉类
        ("羊排", 250),
        # 鱼类
        ("三文鱼", 200), ("ハマチ（鰤鱼）", 200), ("金鲳鱼", 300), ("带鱼", 200),
        ("海鲈鱼", 400), ("黑鱼片", 200), ("脆鱼片", 200), ("黄鳝丝", 200), ("鳗鱼", 200),
        # 海鲜类
        ("鱿鱼", 200), ("章鱼", 200), ("白虾", 200), ("北极虾", 200), ("虾排", 200),
        ("扇贝粉", 100),
    ]
    for name, portion_g in proteins:
        add(name, "protein", "quantity", frozen=1, portion_g=portion_g)

    # ── 干货柜 (dry_goods, boolean) ───────────────────────────
    for name in ["海草", "粉丝", "腐竹", "木耳", "干香菇", "虾皮", "海米", "魔芋", "豆腐皮"]:
        add(name, "dry_goods", "boolean")

    # ── 调味/香料 (seasoning, boolean) ───────────────────────
    add("葱", "seasoning", "boolean", notes="常备，容易忘记补")
    add("香菜", "seasoning", "boolean")
    add("冻姜", "seasoning", "boolean", frozen=1, notes="冷冻")
    add("冻蒜", "seasoning", "boolean", frozen=1, notes="冷冻")

    return rows


def _meal_preset_seeds():
    breakfast_items = {
        "estimated_kcal": 580,
        "estimated_protein_g": 30,
        "estimated_fiber_g": 16,
        "items": [
            {"name": "干杂豆（黑豆/鹰嘴豆/绿小扁豆混合）", "amount": 35, "unit": "g"},
            {"name": "发芽钢切燕麦", "amount": 25, "unit": "g"},
            {"name": "三色藜麦荞麦mix", "amount": 15, "unit": "g"},
            {"name": "块茎（紫薯/南瓜/山药/红薯随机）", "amount": 80, "unit": "g"},
            {"name": "奇亚籽/亚麻籽/火麻仁（轮转）", "amount": 15, "unit": "g"},
            {"name": "燕麦麸皮", "amount": 10, "unit": "g"},
            {"name": "水煮鸡蛋（大）", "amount": 1, "unit": "个"},
            {"name": "混合水果", "amount": 200, "unit": "g"},
            {"name": "自制欧蕾（黑咖啡+2%超滤牛奶）", "amount": 390, "unit": "ml"},
        ],
    }
    lunch_items = {
        "estimated_kcal": 210,
        "estimated_protein_g": 15,
        "estimated_fiber_g": 2,
        "items": [
            {"name": "2%超滤牛奶", "amount": 300, "unit": "ml"},
            {"name": "未碱化可可粉", "amount": 10, "unit": "g"},
            {"name": "混合坚果", "amount": 10, "unit": "g"},
        ],
    }
    return [
        (_uid(), "默认早餐", "breakfast", json.dumps(breakfast_items, ensure_ascii=False), 1),
        (_uid(), "默认午餐", "lunch",     json.dumps(lunch_items, ensure_ascii=False),     1),
    ]


def _user_settings_seeds():
    return [
        ("weight_a", "65"),                # Person A body weight (kg) — placeholder
        ("weight_b", "55"),                # Person B body weight (kg) — placeholder
        ("protein_multiplier_min", "1.2"),
        ("protein_multiplier_max", "1.5"),
        ("target_kcal_per_day", "1700"),   # per person, per day
        ("language", "zh"),
        ("max_wok_dishes", "1"),
        ("condiment_intake_ratio", "0.25"),  # fraction of condiment mass actually consumed
    ]


# ─────────────────────────────────────────────────────────────
# Migration (safe to run on existing databases)
# ─────────────────────────────────────────────────────────────

def migrate_database() -> None:
    """Apply schema changes to existing databases. Idempotent."""
    conn = get_connection()
    try:
        # 1. ingredients: add intake_ratio if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ingredients)").fetchall()]
        if "intake_ratio" not in cols:
            conn.execute("ALTER TABLE ingredients ADD COLUMN intake_ratio REAL NOT NULL DEFAULT 1.0")
            print("  migration: added ingredients.intake_ratio")

        # 2. recipes.category: convert plain string → JSON array for existing rows
        #    Only touch rows where category doesn't start with '[' (i.e. old format)
        old_rows = conn.execute(
            "SELECT id, category FROM recipes WHERE category NOT LIKE '[%'"
        ).fetchall()
        if old_rows:
            # Map old single-value categories to new list format
            cat_map = {
                "荤菜": ["荤菜"], "素菜": ["纯素"], "凉菜": ["纯素", "凉拌"],
                "主食": ["主食"], "汤": ["荤菜", "汤"],
            }
            for row in old_rows:
                new_cat = json.dumps(
                    cat_map.get(row["category"], [row["category"]]),
                    ensure_ascii=False
                )
                conn.execute("UPDATE recipes SET category=? WHERE id=?", (new_cat, row["id"]))
            print(f"  migration: converted {len(old_rows)} recipes.category to JSON array")

        # 3. inventory: convert unit '份' → 'g' for quantity items
        conn.execute("UPDATE inventory SET unit='g' WHERE item_type='quantity' AND unit='份'")

        # 4. recipes: add condiment_ratio column
        cols = [r[1] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()]
        if "condiment_ratio" not in cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN condiment_ratio REAL NOT NULL DEFAULT 1.0")
            print("  migration: added recipes.condiment_ratio")

        # 7. recipes: reset condiment_ratio default 0.25→1.0 for all unmodified rows
        n_reset = conn.execute(
            "UPDATE recipes SET condiment_ratio=1.0 WHERE condiment_ratio=0.25"
        ).rowcount
        if n_reset:
            print(f"  migration: reset {n_reset} recipes.condiment_ratio 0.25→1.0")

        # 8. Migrate form categories: 汤/凉拌 → cooking_method; add 菜肴 default
        _FORM_NEW = {"菜肴", "主食", "甜点", "早餐", "饮料", "冷冻", "预制"}
        rows_cat = conn.execute("SELECT id, category, cooking_method FROM recipes").fetchall()
        n_cat_migrated = 0
        for row in rows_cat:
            cat     = json.loads(row["category"]       or "[]")
            methods = json.loads(row["cooking_method"] or "[]")
            if any(c in _FORM_NEW for c in cat):
                continue  # already has a new form tag
            changed = False
            if "汤" in cat:
                cat.remove("汤")
                if "汤" not in methods:
                    methods.append("汤")
                changed = True
            if "凉拌" in cat:
                cat.remove("凉拌")
                if "凉拌" not in methods:
                    methods.append("凉拌")
                changed = True
            if not any(c in _FORM_NEW for c in cat):
                cat.append("菜肴")
                changed = True
            if changed:
                conn.execute(
                    "UPDATE recipes SET category=?, cooking_method=? WHERE id=?",
                    (json.dumps(cat, ensure_ascii=False),
                     json.dumps(methods, ensure_ascii=False),
                     row["id"]),
                )
                n_cat_migrated += 1
        if n_cat_migrated:
            print(f"  migration: migrated {n_cat_migrated} recipes to new form categories")

        # 9. recipes: add en_name / en_desc / zh_desc for restaurant-style menu
        for col in ("en_name", "en_desc", "zh_desc"):
            cols = [r[1] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE recipes ADD COLUMN {col} TEXT")
                print(f"  migration: added recipes.{col}")

        # 11. daily_logs: dinner_staple, ingredients_snapshot, total_nutrients_json
        dl_cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_logs)").fetchall()]
        for col in ("dinner_staple", "ingredients_snapshot", "total_nutrients_json"):
            if col not in dl_cols:
                conn.execute(f"ALTER TABLE daily_logs ADD COLUMN {col} TEXT")
                print(f"  migration: added daily_logs.{col}")

        # 12. recipes: add serving_ratio (default 1.0 = eat full portion)
        r_cols = [r[1] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()]
        if "serving_ratio" not in r_cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN serving_ratio REAL NOT NULL DEFAULT 1.0")
            print("  migration: added recipes.serving_ratio")

        # 13. recipes: split cook_time_min → active_time_min + idle_time_min
        r_cols = [r[1] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()]
        if "active_time_min" not in r_cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN active_time_min INTEGER")
            conn.execute("ALTER TABLE recipes ADD COLUMN idle_time_min INTEGER NOT NULL DEFAULT 0")
            # seed active_time from existing cook_time_min
            conn.execute("UPDATE recipes SET active_time_min = cook_time_min WHERE cook_time_min IS NOT NULL")
            conn.execute("UPDATE recipes SET active_time_min = 30 WHERE active_time_min IS NULL")
            print("  migration: added recipes.active_time_min / idle_time_min")

        # 14. recipes: cuisine (菜系联动) + pairing_ids (强搭配)
        r_cols = [r[1] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()]
        if "cuisine" not in r_cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN cuisine TEXT")
            print("  migration: added recipes.cuisine")
        if "pairing_ids" not in r_cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN pairing_ids TEXT NOT NULL DEFAULT '[]'")
            print("  migration: added recipes.pairing_ids")

        # 10. inventory: migrate staple_veg → other (category renamed)
        n_staple = conn.execute(
            "UPDATE inventory SET category='other' WHERE category='staple_veg'"
        ).rowcount
        if n_staple:
            print(f"  migration: migrated {n_staple} staple_veg items → other")

        # 5. nutrition_cache: extended micronutrients
        nc_cols = [r[1] for r in conn.execute("PRAGMA table_info(nutrition_cache)").fetchall()]
        for col in ("vitd_per_100g", "vita_per_100g", "magnesium_per_100g", "zinc_per_100g"):
            if col not in nc_cols:
                conn.execute(f"ALTER TABLE nutrition_cache ADD COLUMN {col} REAL")
                print(f"  migration: added nutrition_cache.{col}")

        # 15. nutrition_cache: fat breakdown. Left NULL rather than 0 on purpose —
        #     "not fetched yet" must stay distinguishable from "genuinely zero
        #     saturated fat", otherwise the UI shows a confident 0 for 600+ rows
        #     that simply have no data. Backfill: scripts/backfill_fat_detail.py
        nc_cols = [r[1] for r in conn.execute("PRAGMA table_info(nutrition_cache)").fetchall()]
        for col in ("satfat_per_100g", "monofat_per_100g", "polyfat_per_100g"):
            if col not in nc_cols:
                conn.execute(f"ALTER TABLE nutrition_cache ADD COLUMN {col} REAL")
                print(f"  migration: added nutrition_cache.{col}")

        # 6. recipes: add steps column (JSON array of step strings)
        if "steps" not in cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN steps TEXT NOT NULL DEFAULT '[]'")
            print("  migration: added recipes.steps")

        conn.commit()
    finally:
        conn.close()




def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # safer concurrent writes
    return conn


def init_database() -> None:
    """Create tables and insert seed data if the DB is empty."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    # Apply any pending migrations (safe on fresh DB too)
    migrate_database()

    conn = get_connection()
    try:

        # ── Inventory ─────────────────────────────────────────
        existing = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        if existing == 0:
            conn.executemany(
                """INSERT OR IGNORE INTO inventory
                   (id, name, category, item_type, in_stock, quantity, unit,
                    is_perishable, is_frozen, portion_weight_g, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                _inventory_seeds(),
            )

        # ── Meal presets ──────────────────────────────────────
        existing = conn.execute("SELECT COUNT(*) FROM meal_presets").fetchone()[0]
        if existing == 0:
            conn.executemany(
                """INSERT OR IGNORE INTO meal_presets
                   (id, name, meal_type, items, is_default)
                   VALUES (?,?,?,?,?)""",
                _meal_preset_seeds(),
            )

        # ── User settings ─────────────────────────────────────
        conn.executemany(
            "INSERT OR IGNORE INTO user_settings (key, value) VALUES (?,?)",
            _user_settings_seeds(),
        )

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
    conn = get_connection()
    inv_count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
    preset_count = conn.execute("SELECT COUNT(*) FROM meal_presets").fetchone()[0]
    conn.close()
    print(f"✅ Database initialized: {DB_PATH}")
    print(f"   inventory rows : {inv_count}")
    print(f"   meal_presets   : {preset_count}")
