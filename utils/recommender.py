"""
Local recipe recommendation engine (Optimized).

Target combo structure (combo_size=4):  1汤 + 1凉拌 + 2热菜
  - Soup and cold slots are best-effort.
  - Hot slots: stratified by (纯蛋白 -> 半蛋白半素 -> 纯素).
  - Inventory: Supports 1-5 scale and null (permanent stock).
"""
import random
from datetime import datetime, timedelta
from typing import Optional

from utils.cache import (
    get_all_inventory_cached as get_all_inventory,
    get_all_recipes_cached as get_all_recipes,
    get_all_ingredients_grouped_cached as get_all_ingredients_grouped,
)

_DIFF_RANK     = {"简单": 0, "中等": 1, "繁琐": 2}
_REQUIRED_CATS = ["纯蛋白", "半蛋白半素", "纯素"]
_DINNER_CATS   = {"菜肴", "主食"}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s: return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def _weighted_sample(pool: list, k: int, rng: random.Random) -> list:
    pool = list(pool)
    result = []
    for _ in range(k):
        if not pool: break
        total = sum(w for w, _ in pool)
        if total <= 0: break
        r = rng.uniform(0, total)
        cumul = 0.0
        chosen = len(pool) - 1
        for i, (w, _) in enumerate(pool):
            cumul += w
            if r <= cumul:
                chosen = i
                break
        result.append(pool[chosen][1])
        pool.pop(chosen)
    return result

def _build_availability(inv: dict) -> tuple:
    """
    Revised to support:
    - quantity is None (Permanent/常备)
    - quantity in 1-5 scale
    - protein exclusion from high_stock logic
    """
    qty_avail:   set = set()
    bool_avail:  set = set()
    perishables: set = set()
    high_stock:  set = set()

    # 1. Process items with numeric/null quantity (Vegetables and Proteins)
    for cat in ("leafy_veg", "protein"):
        for item in inv.get(cat, []):
            name = item["name"]
            q = item.get("quantity")
            
            # Logic: Available if q is None (常备) or q > 0
            is_available = (q is None) or (isinstance(q, (int, float)) and q > 0)
            
            if is_available:
                qty_avail.add(name)
                
                # High stock/Perishable logic ONLY for leafy vegetables
                if cat == "leafy_veg":
                    if item.get("is_perishable"):
                        perishables.add(name)
                    # Threshold updated to 5 for the new 1-5 scale
                    if q is not None and isinstance(q, (int, float)) and q >= 5:
                        high_stock.add(name)

    # 2. Process boolean-based items
    for cat in ("dry_goods", "seasoning", "other"):
        for item in inv.get(cat, []):
            if item.get("in_stock"):
                bool_avail.add(item["name"])

    return qty_avail, bool_avail, perishables, high_stock

def _recipe_flags(r: dict) -> tuple:
    t       = r.get("cook_time_min") or 99
    wok     = bool(r.get("uses_wok"))
    methods = r.get("cooking_method") or []
    return (
        wok and t > 5,
        wok and t <= 5,
        "汤"   in methods,
        "凉拌" in methods,
    )

def _score(recipe: dict, main_ings: list, perishables: set, high_stock: set,
           wishlist_ids: Optional[set] = None) -> float:
    s = 1.0
    perishable_boost = 0.0
    for name in main_ings:
        if name in perishables:
            # Reduced from +3.0 to +1.5 per perishable ing (was causing 100%
            # appearance of perishable-bearing dishes — 4× weight crushed alts)
            perishable_boost += 1.5
        if name in high_stock:
            s += 1.0
    # Cap total perishable stacking at +3.0 per recipe (was unlimited — a dish
    # with 3 perishables would hit 10×; now max is 2× boost ceiling)
    s += min(perishable_boost, 3.0)

    if recipe.get("is_parallel"):
        s += 1.5

    # Wishlist soft preference: items the user explicitly marked "想做" get a
    # strong nudge (same magnitude as pairing anchor) so they naturally surface
    # in recommendations rather than getting forgotten in the wishlist page.
    if wishlist_ids and recipe.get("id") in wishlist_ids:
        s += 5.0

    sw, lw, sp, co = _recipe_flags(recipe)
    # Penalty for multi-method recipes to keep things simple
    if sum([sw, lw, sp, co]) >= 2:
        s = max(1.0, s * 0.4)
    return s

def _pick_one(pool: list, used_ids: set, std_wok_cnt: int, lgt_wok_cnt: int, rng: random.Random) -> Optional[object]:
    avail = []
    for w, item in pool:
        r = item[0]
        if r["id"] in used_ids: continue
        sw, lw, _, _ = _recipe_flags(r)
        if sw and std_wok_cnt >= 1: continue
        if lw and lgt_wok_cnt >= 1: continue
        avail.append((w, item))
    if not avail: return None
    chosen = _weighted_sample(avail, 1, rng)
    return chosen[0] if chosen else None

def _dynamic_boost(pool: list, selected: list) -> list:
    """Re-weight pool given already-selected dishes:
    - same cuisine as any selected → ×2.5 (Cuisine Resonance)
    - id is in any selected recipe's pairing_ids → +5.0 (Recipe Anchoring)
    """
    if not selected:
        return pool

    cuisines: set = set()
    pairings: set = set()
    for item in selected:
        r = item[0]
        c = r.get("cuisine")
        if c: cuisines.add(c)
        for pid in (r.get("pairing_ids") or []):
            pairings.add(pid)

    if not cuisines and not pairings:
        return pool

    boosted = []
    for w, item in pool:
        r = item[0]
        nw = w
        if cuisines and r.get("cuisine") in cuisines:
            nw *= 2.5
        if r["id"] in pairings:
            nw += 5.0
        boosted.append((nw, item))
    return boosted


def _build_combo(
    dish_pools:  dict,
    cat_pools:   dict,
    active_cats: list,
    full_pool:   list,
    combo_size:  int,
    rng:         random.Random,
    excluded:    set,
) -> Optional[list]:
    selected:    list = []
    used_ids:    set  = excluded.copy()
    std_wok_cnt: int  = 0
    lgt_wok_cnt: int  = 0

    def _take(pool) -> bool:
        nonlocal std_wok_cnt, lgt_wok_cnt
        # Apply cuisine resonance + pairing boost based on what's already picked
        pool = _dynamic_boost(pool, selected)
        item = _pick_one(pool, used_ids, std_wok_cnt, lgt_wok_cnt, rng)
        if item is None: return False
        r = item[0]
        selected.append(item)
        used_ids.add(r["id"])
        sw, lw, _, _ = _recipe_flags(r)
        if sw: std_wok_cnt += 1
        if lw: lgt_wok_cnt += 1
        return True

    # Slot 1 & 2
    _take(dish_pools["汤"])
    _take(dish_pools["凉拌"])

    covered_meat = {
        c for item in selected
        for c in item[0].get("category", [])
        if c in set(active_cats)
    }
    
    # ⚠️ FIX: Removed rng.shuffle(uncovered) to respect the passed active_order 
    # (where 纯蛋白 is prioritized at the front)
    uncovered = [c for c in active_cats if c not in covered_meat]

    for cat in uncovered:
        if len(selected) >= combo_size: break
        _take(cat_pools.get(cat, []))

    while len(selected) < combo_size:
        if not _take(dish_pools["热菜"]) and not _take(full_pool):
            return None 
    return selected[:combo_size]

def _validate(combo: list, required_cats: set) -> list:
    violations = []
    std   = [r for r in combo if r.get("uses_wok") and (r.get("active_time_min") or r.get("cook_time_min") or 99) > 5]
    light = [r for r in combo if r.get("uses_wok") and (r.get("active_time_min") or r.get("cook_time_min") or 99) <= 5]
    if len(std) > 1: violations.append(f"炒锅冲突（{len(std)} 道标准占锅菜）")
    if len(light) > 1: violations.append(f"轻占锅过多（{len(light)} 道）")

    all_cats: set = set()
    for r in combo: all_cats.update(r.get("category", []))
    for cat in required_cats:
        if cat not in all_cats: violations.append(f"缺少{cat}菜")

    if sum(1 for r in combo if r.get("prep_difficulty") == "繁琐") > 1:
        violations.append("难度过高（超过 1 道繁琐菜）")
    return violations

def _combo_stats(combo: list) -> dict:
    par_max  = max((r.get("cook_time_min") or 0 for r in combo if r.get("is_parallel")), default=0)
    nonpar   = sum(r.get("cook_time_min") or 0 for r in combo if not r.get("is_parallel"))
    seq_time = sum(r.get("cook_time_min") or 0 for r in combo)
    par_time = par_max + nonpar
    diffs      = [_DIFF_RANK.get(r.get("prep_difficulty", "简单"), 0) for r in combo]
    diff_label = ["简单", "中等", "繁琐"][max(diffs)]
    all_methods: set = set()
    for r in combo: all_methods.update(r.get("cooking_method") or [])

    # Cuisine coherence: majority cuisine if ≥2 dishes share it
    cuisine_counts: dict = {}
    for r in combo:
        c = r.get("cuisine")
        if c: cuisine_counts[c] = cuisine_counts.get(c, 0) + 1
    dominant_cuisine = None
    if cuisine_counts:
        top = max(cuisine_counts.items(), key=lambda x: x[1])
        if top[1] >= 2:
            dominant_cuisine = top[0]

    return {
        "seq_time":         seq_time,
        "par_time":         par_time,
        "difficulty":       diff_label,
        "has_soup":         "汤" in all_methods,
        "has_cold":         "凉拌" in all_methods,
        "has_parallel":     any(r.get("is_parallel") for r in combo),
        "dominant_cuisine": dominant_cuisine,
        "cuisine_counts":   cuisine_counts,
    }

# ── Main Entry ────────────────────────────────────────────────────────────────

def recommend(
    n_combos: int = 2,
    combo_size: int = 4,
    max_attempts: int = 600,
    seed_val=None,
    max_single_dish_min: Optional[int] = None,
    wishlist_boost_ids: Optional[set] = None,
) -> list:
    """
    max_single_dish_min: if set, exclude any recipe whose total wall-clock time
        (active_time_min + idle_time_min) exceeds this budget.
    wishlist_boost_ids: recipe IDs that should get +5.0 score boost (user's
        🌟 想做菜 with today/past/no target_date). Future-dated wishlist items
        should NOT be in this set — caller filters by date awareness.
    """
    inv = get_all_inventory()
    qty_avail, bool_avail, perishables, high_stock = _build_availability(inv)
    all_avail = qty_avail | bool_avail

    now = datetime.now()
    cutoff_48h = now - timedelta(hours=48)
    cutoff_24h = now - timedelta(hours=24)

    all_recipes = get_all_recipes()
    # Bulk-fetch ALL ingredients in a single SQL — fixes the N+1 query pattern
    # that previously dominated runtime (~640ms for 250 recipes → ~20ms now).
    all_ings = get_all_ingredients_grouped()

    recently_consumed: set = set()
    for r in all_recipes:
        lc = _parse_dt(r.get("last_cooked"))
        if lc and lc > cutoff_24h:
            for ing in all_ings.get(r["id"], []):
                if ing["name"] in perishables:
                    recently_consumed.add(ing["name"])

    must_cover = perishables - recently_consumed

    pool: list = []
    for r in all_recipes:
        if not any(c in _DINNER_CATS for c in r.get("category", [])): continue
        lc = _parse_dt(r.get("last_cooked"))
        if lc and lc > cutoff_48h: continue

        # Time budget — exclude dishes whose total wall-clock exceeds budget
        if max_single_dish_min is not None:
            total_t = (r.get("active_time_min") or 0) + (r.get("idle_time_min") or 0)
            if total_t > max_single_dish_min:
                continue

        ings      = all_ings.get(r["id"], [])
        main_ings = [i["name"] for i in ings if not i.get("is_condiment")]
        if not all(name in all_avail for name in main_ings): continue

        w = _score(r, main_ings, perishables, high_stock, wishlist_boost_ids)
        pool.append((w, (dict(r), main_ings)))

    if len(pool) < combo_size: return []

    dish_pools: dict = {"汤": [], "凉拌": [], "热菜": []}
    for w, item in pool:
        methods = item[0].get("cooking_method") or []
        if "汤" in methods: dish_pools["汤"].append((w, item))
        elif "凉拌" in methods: dish_pools["凉拌"].append((w, item))
        else: dish_pools["热菜"].append((w, item))

    cat_pools_hot: dict = {cat: [] for cat in _REQUIRED_CATS}
    for w, item in dish_pools["热菜"]:
        for cat in _REQUIRED_CATS:
            if cat in item[0].get("category", []):
                cat_pools_hot[cat].append((w, item))

    active_cats   = [cat for cat in _REQUIRED_CATS if cat_pools_hot[cat]]
    required_cats = set(active_cats)
    tail = [c for c in ["半蛋白半素", "纯素"] if c in active_cats]

    rng           = random.Random(seed_val)
    combos: list  = []
    accepted_keys: set = set()
    accepted_ids:  set = set()

    def _run(n_tries: int, enforce_disjoint: bool) -> None:
        tried: set = set()
        for _ in range(n_tries):
            if len(combos) >= n_combos: return

            shuffled_tail = list(tail)
            rng.shuffle(shuffled_tail)
            # "纯蛋白" is kept at the beginning of active_order
            active_order = (["纯蛋白"] if "纯蛋白" in active_cats else []) + shuffled_tail

            excluded = accepted_ids if enforce_disjoint else set()
            sampled  = _build_combo(
                dish_pools, cat_pools_hot, active_order, pool,
                combo_size, rng, excluded,
            )
            if sampled is None or len(sampled) < combo_size: continue

            recipes = [item[0] for item in sampled]
            key     = frozenset(r["id"] for r in recipes)
            if key in tried or key in accepted_keys: continue
            tried.add(key)

            if _validate(recipes, required_cats): continue

            # Compute perishable coverage for warnings only — no longer hard-
            # enforced. The score boost (_score: +1.5/perishable, cap +3.0)
            # provides soft preference. Single-perishable users will still
            # see it ~70-80% of the time but not 100%.
            covered: set = set()
            for r in recipes:
                for ing in all_ings.get(r["id"], []):
                    if ing["name"] in must_cover: covered.add(ing["name"])
            uncovered = must_cover - covered

            warnings = []
            if uncovered: warnings.append(f"⚠️ 未覆盖易坏食材：{'、'.join(sorted(uncovered))}")

            accepted_keys.add(key)
            combos.append({
                "recipes":               recipes,
                "stats":                 _combo_stats(recipes),
                "warnings":              warnings,
                "covers_perishables":    sorted(covered),
                "uncovered_perishables": sorted(uncovered),
            })
            accepted_ids.update(r["id"] for r in recipes)

    # Pass 1: try to find disjoint combos (different recipes between combos)
    # Pass 2: fallback allowing overlap between accepted combos
    _run(max_attempts, enforce_disjoint=True)
    if len(combos) < n_combos: _run(max_attempts, enforce_disjoint=False)

    return combos