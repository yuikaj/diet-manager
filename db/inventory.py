"""CRUD operations for inventory and prepared foods tables."""
import uuid
from typing import Optional
from db.init_db import get_connection


# ─── Inventory ────────────────────────────────────────────────

def get_all_inventory() -> dict:
    """Return {category: [row_dict, ...]} sorted by name within each category."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM inventory ORDER BY category, name"
    ).fetchall()
    conn.close()

    result: dict = {}
    for row in rows:
        cat = row["category"]
        result.setdefault(cat, []).append(dict(row))
    return result


def toggle_in_stock(item_id: str, value: bool) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE inventory SET in_stock=?, updated_at=datetime('now','localtime') WHERE id=?",
        (1 if value else 0, item_id),
    )
    conn.commit()
    conn.close()


def set_quantity(item_id: str, quantity: float) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE inventory SET quantity=?, updated_at=datetime('now','localtime') WHERE id=?",
        (max(0.0, quantity), item_id),
    )
    conn.commit()
    conn.close()


def add_item(
    name: str,
    category: str,
    item_type: str,
    *,
    is_perishable: bool = False,
    is_frozen: bool = False,
    portion_weight_g: float = 200,
    notes: Optional[str] = None,
) -> str:
    item_id = str(uuid.uuid4())
    in_stock = 0 if item_type == "boolean" else None
    quantity = 0.0 if item_type == "quantity" else None
    # `quantity` counts 份, not grams (grams come from portion_weight_g), so the
    # unit label has to say 份 — matching the column default and every UI string.
    unit = "份" if item_type == "quantity" else None
    conn = get_connection()
    conn.execute(
        """INSERT INTO inventory
           (id, name, category, item_type, in_stock, quantity, unit,
            is_perishable, is_frozen, portion_weight_g, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (item_id, name, category, item_type, in_stock, quantity, unit,
         1 if is_perishable else 0, 1 if is_frozen else 0, portion_weight_g, notes),
    )
    conn.commit()
    conn.close()
    return item_id


def delete_item(item_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def set_portion_weight(item_id: str, grams: float) -> None:
    """How many grams one 份 of this item weighs.

    Varies a lot per item (一根黄瓜 vs 一斤青菜 vs 整条鱼), so it is set per row
    rather than derived from the category. Feeds the ≈Xg label on each row and
    the 天数 estimate at the top of the page.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE inventory SET portion_weight_g=?, updated_at=datetime('now','localtime') WHERE id=?",
        (max(1.0, float(grams)), item_id),
    )
    conn.commit()
    conn.close()


def toggle_perishable(item_id: str, value: bool) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE inventory SET is_perishable=? WHERE id=?",
        (1 if value else 0, item_id),
    )
    conn.commit()
    conn.close()


# ─── Prepared foods ───────────────────────────────────────────

def get_all_prepared_foods() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM prepared_foods ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_prepared_food(
    name: str,
    *,
    brand: Optional[str] = None,
    inventory_count: int = 1,
    is_frozen: bool = True,
) -> str:
    pf_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        """INSERT INTO prepared_foods (id, name, brand, inventory_count, is_frozen)
           VALUES (?,?,?,?,?)""",
        (pf_id, name, brand, inventory_count, 1 if is_frozen else 0),
    )
    conn.commit()
    conn.close()
    return pf_id


def update_prepared_count(pf_id: str, count: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE prepared_foods SET inventory_count=? WHERE id=?",
        (max(0, count), pf_id),
    )
    conn.commit()
    conn.close()


def delete_prepared_food(pf_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM prepared_foods WHERE id=?", (pf_id,))
    conn.commit()
    conn.close()
