# Diet Manager

A personal home-kitchen management system built with Streamlit. Tracks pantry inventory, maintains a recipe library, plans dinners with smart recommendations, monitors full-day nutrition against DRI targets, and prints restaurant-style weekly menus as PDF.

## Engineering highlights

A full end-to-end AI application built solo:

- **LLM structured extraction** — paste free-form recipe notes; Gemini parses them into typed records (gram weights with density-aware unit conversion, steps, categories, condiment ratios).
- **Semantic search** — ChromaDB + sentence-transformers index over the recipe library for natural-language lookup.
- **Layered data with graceful fallback** — a 4-tier nutrition lookup (SQLite cache → curated JSON → USDA API → custom) keeps results fast and correct, with the live API as last resort.
- **Clean separation** — `db/` (CRUD) · `utils/` (lookup, recommender, semantic search, advisor, PDF) · `views/` (Streamlit UI), with an idempotent, migration-chained schema initializer.

## Features

| Module | What it does |
|--------|-------------|
| **Pantry** | Five categories (leafy veg / protein+frozen / seasoning / dry goods / other); gram tracking with ±453g/±200g quick buttons; boolean in-stock toggles; shopping mode for bulk entry; perishable flagging |
| **Recipe Library** | Full CRUD with ingredients, steps, cooking methods, and categories; per-recipe condiment intake ratio and personal serving ratio; ChromaDB semantic search; **⚡ AI onboarding** — paste free-form text, Gemini parses and inserts in one flow |
| **Dinner Planner** | Auto-recommend 1 cold dish + 2 hot dishes + 1 soup; pantry-aware filter (show only cookable recipes); manual override; placeholder slots; nutrition preview; inventory deduction |
| **Nutrition Analysis** | Four-tier lookup (SQLite cache → local JSON → USDA API → custom); DRI progress bars; AI nutrition advisor (Gemini, 3×/day) |
| **Daily Nutrition** | Breakfast/lunch with skip toggle and custom ingredient override; fruit selection; dinner from plan with per-recipe serving ratio; optional staple (rice / none / custom); full-day DRI tracking; daily log; 7-day analysis with ingredient diversity and DRI heatmap |
| **PDF Menu** | Restaurant-style front (Chinese + English name + poetic tagline); 2×2 recipe execution cards on back; full-day nutrition summary pinned to footer |

## Setup

```bash
# 1. Install dependencies (Python 3.9 required)
pip3.9 install -r requirements.txt

# 2. Copy env template and add your API keys
cp .env.example .env   # or create .env manually
# Required: GEMINI_API_KEY, USDA_API_KEY

# 3. Initialize the database (idempotent, safe to re-run)
python3.9 db/init_db.py

# 4. Launch the app
python3.9 -m streamlit run app.py
```

### First-time semantic search setup

```bash
# Build the ChromaDB recipe index (run once, or click the button in the UI)
python3.9 scripts/build_recipe_embeddings.py
```

## Environment Variables

```
GEMINI_API_KEY=...    # Google AI Studio key (for AI scripts + nutrition advisor + AI onboarding)
USDA_API_KEY=...      # USDA FoodData Central key (free at fdc.nal.usda.gov)
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/clean_recipes_ai.py` | Batch-clean raw recipe data via Gemini (ingredients, steps, category) |
| `scripts/seed_nutrition_ai.py` | Batch-fill nutrition data via Gemini + USDA grounding |
| `scripts/gen_recipe_descriptions.py` | Generate English names and taglines for recipes (for PDF menu) |
| `scripts/build_recipe_embeddings.py` | One-time index all recipes into ChromaDB |

### gen_recipe_descriptions.py

```bash
python3.9 scripts/gen_recipe_descriptions.py              # all recipes missing en_name
python3.9 scripts/gen_recipe_descriptions.py --force      # re-generate all
python3.9 scripts/gen_recipe_descriptions.py --recipe 红烧肉  # single recipe
python3.9 scripts/gen_recipe_descriptions.py --dry-run    # list targets only
```

### seed_nutrition_ai.py

```bash
python3.9 scripts/seed_nutrition_ai.py                    # fill all missing nutrition
python3.9 scripts/seed_nutrition_ai.py --recipe 红烧肉    # one recipe
python3.9 scripts/seed_nutrition_ai.py --ingredient 猪五花 # one ingredient
python3.9 scripts/seed_nutrition_ai.py --manual-update --data '{"name":"鸡蛋","calories_per_100g":143}'
```

## Architecture

```
diet-manager/
├── app.py                    # Streamlit entry point, sidebar nav, _nav_pending jump
├── config.py                 # Shared constants (DRI, colors)
├── db/
│   ├── init_db.py            # Schema init + migration chain (steps 1-12, idempotent)
│   ├── recipes.py            # Recipe/ingredient CRUD
│   ├── daily_log.py          # Daily log CRUD (dinner_staple, snapshot, nutrients)
│   ├── inventory.py          # Inventory CRUD (5 categories)
│   └── nutrition.py          # Nutrition cache CRUD
├── utils/
│   ├── nutrition_lookup.py   # Four-tier nutrition lookup engine
│   ├── recommender.py        # Weighted random recommender, structured slot filling
│   ├── semantic_search.py    # ChromaDB semantic search (sentence-transformers)
│   ├── nutrition_advisor.py  # Gemini nutrition advisor, 3×/day rate limit
│   └── pdf_generator.py      # reportlab PDF (PingFang font, restaurant style)
├── views/
│   ├── inventory.py          # Pantry UI (5 tabs, shopping mode, perishable flags)
│   ├── recipes.py            # Recipe library UI (CRUD + AI onboarding + semantic search)
│   ├── plan.py               # Dinner planner UI (recommender + pantry filter)
│   └── nutrition.py          # Nutrition analysis + daily tracker + 7-day heatmap
├── data/
│   ├── diet.db               # SQLite database
│   ├── local_nutrition.json  # Hand-curated nutrition overrides
│   ├── ingredient_translations.json  # Chinese→English for USDA queries
│   └── chroma/               # ChromaDB persistent store
└── scripts/                  # Offline data-prep utilities
```

## Recipe Category System

Recipes use two orthogonal dimensions stored in the `category` JSON array:

- **Meat dimension** (optional): `荤` / `素` / `荤素` — used by the recommender for variety
- **Form dimension** (single): `菜肴` / `主食` / `甜点` / `早餐` / `饮料` / `冷冻` / `预制`

`汤` and `凉拌` are stored in the `cooking_method` field, not category. The dinner recommender only draws from `菜肴` and `主食`, targeting a structure of 1 cold dish + 2 hot dishes + 1 soup.

## Per-Recipe Nutrition Scaling

Two sliders in the recipe editor control how ingredients contribute to the daily nutrition calculation:

- **condiment_ratio** (0–100%): fraction of condiments actually consumed (e.g. 15% for a braise where you discard the sauce)
- **serving_ratio** (0–100%): personal portion size relative to a half-recipe (e.g. 50% if you only eat a small bowl of a large-batch dish)

Dinner nutrition per person = `ingredient_amount × condiment_ratio (for condiments) × serving_ratio ÷ 2`

## AI Recipe Onboarding

Click **⚡ AI 入库** in the recipe library header to add a new recipe from free-form text:

1. Paste your notes in any format (ingredients with amounts, steps, no structure required)
2. Gemini (`gemini-2.0-flash`) parses it into structured data: ingredients with gram weights (density-aware unit conversion), steps with inline ingredient weights, categories, cooking method, condiment ratio, and bilingual descriptions
3. Preview the result, confirm to insert — ChromaDB index updated automatically
4. Trigger nutrition lookup for new ingredients (4-tier fallback; missing ones show a CLI hint)

## Nutrition Lookup Priority

1. SQLite cache (`nutrition_cache` table) — fastest, editable in UI
2. `data/local_nutrition.json` — hand-curated overrides for USDA mismatch corrections
3. USDA FoodData Central API — live lookup, result cached to SQLite
4. User-defined custom entries — added via seed script or UI

## Pantry Categories

| Category | Type | Step | Notes |
|----------|------|------|-------|
| 叶菜/时令 (`leafy_veg`) | Gram quantity | 500g | Perishable flag supported |
| 蛋白/冷库 (`protein`) | Gram quantity | 300g | Includes frozen and prepared items |
| 调味 (`seasoning`) | Boolean | — | Click to toggle in-stock |
| 干货 (`dry_goods`) | Boolean | — | Click to toggle in-stock |
| 其他 (`other`) | Boolean | — | Root veg, pantry staples, misc |

## PDF Menu

The printed menu has two sides:
- **Front**: Date header, each dinner dish listed as ◆ 中文名 / English Name / tagline, full-day nutrition summary pinned to bottom (includes Ca, Fe, VitC, K)
- **Back**: 2×2 grid of recipe execution cards (ingredients + steps)

Print order: cold dishes → hot dishes → soup.
