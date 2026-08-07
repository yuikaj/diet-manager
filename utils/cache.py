"""Streamlit-layer caching wrappers around hot, repeatedly-called DB reads.

Kept out of db/ on purpose — the db/ layer stays framework-agnostic.
Each write wrapper here mutates via the real db function then invalidates
the matching cache, so callers never have to remember to invalidate by hand.
"""
import streamlit as st
from db.recipes import (
    get_all_recipes as _get_all_recipes,
    get_all_ingredients_grouped as _get_all_ingredients_grouped,
    create_recipe as _create_recipe,
    update_recipe as _update_recipe,
    delete_recipe as _delete_recipe,
    mark_cooked as _mark_cooked,
)
from db.inventory import (
    get_all_inventory as _get_all_inventory,
    toggle_in_stock as _toggle_in_stock,
    set_quantity as _set_quantity,
    add_item as _add_item,
    delete_item as _delete_item,
    toggle_perishable as _toggle_perishable,
    set_portion_weight as _set_portion_weight,
)


# ─── Recipes ────────────────────────────────────────────────

@st.cache_data(ttl=30, show_spinner=False)
def get_all_recipes_cached(category=None, data_quality=None, cooking_method=None, search=None):
    return _get_all_recipes(
        category=category,
        data_quality=data_quality,
        cooking_method=cooking_method,
        search=search,
    )


@st.cache_data(ttl=30, show_spinner=False)
def get_all_ingredients_grouped_cached():
    return _get_all_ingredients_grouped()


def invalidate_recipes_cache() -> None:
    """Call after any recipe/ingredient create/update/delete/mark_cooked."""
    get_all_recipes_cached.clear()
    get_all_ingredients_grouped_cached.clear()


# Auto-invalidating write wrappers. Import these instead of the raw db.recipes
# functions: relying on every call site to remember an invalidate() call makes a
# forgotten one a silent stale read, which is near-impossible to notice.
def create_recipe(*args, **kwargs):
    out = _create_recipe(*args, **kwargs)
    invalidate_recipes_cache()
    return out


def update_recipe(*args, **kwargs):
    out = _update_recipe(*args, **kwargs)
    invalidate_recipes_cache()
    return out


def delete_recipe(*args, **kwargs):
    out = _delete_recipe(*args, **kwargs)
    invalidate_recipes_cache()
    return out


def mark_cooked(*args, **kwargs):
    out = _mark_cooked(*args, **kwargs)
    invalidate_recipes_cache()
    return out


# ─── Inventory ──────────────────────────────────────────────

@st.cache_data(ttl=15, show_spinner=False)
def get_all_inventory_cached():
    return _get_all_inventory()


def invalidate_inventory_cache() -> None:
    get_all_inventory_cached.clear()


def toggle_in_stock(item_id: str, value: bool) -> None:
    _toggle_in_stock(item_id, value)
    invalidate_inventory_cache()


def set_quantity(item_id: str, quantity: float) -> None:
    _set_quantity(item_id, quantity)
    invalidate_inventory_cache()


def add_item(*args, **kwargs):
    new_id = _add_item(*args, **kwargs)
    invalidate_inventory_cache()
    return new_id


def delete_item(item_id: str) -> None:
    _delete_item(item_id)
    invalidate_inventory_cache()


def toggle_perishable(item_id: str, value: bool) -> None:
    _toggle_perishable(item_id, value)
    invalidate_inventory_cache()


def set_portion_weight(item_id: str, grams: float) -> None:
    _set_portion_weight(item_id, grams)
    invalidate_inventory_cache()
