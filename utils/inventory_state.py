"""Shared readings of the inventory dict — one definition each, on purpose.

"Is this ingredient available?" was implemented three times (今日规划的可做菜过滤、
食愿之书的缺料判断、推荐器的选菜池) and the recommender's copy disagreed with the
other two: it treated the 常备免记量 rows (item_type='boolean', whose `quantity`
is always NULL) as permanently in stock, ignoring their in_stock flag entirely.
Marking 猪肉末 as ⬜ 缺货 hid it from the picker while the recommender kept
suggesting dishes that needed it.

Same class of bug as the three generations of 调料摄入比例: a rule duplicated
across call sites drifts silently. Import from here instead of re-deriving.
"""

# A 份 count at or above this reads as "囤了不少" — shown as 🟠 in the inventory
# list and given a mild recommendation boost. One number, two consumers.
HIGH_STOCK_PORTIONS = 4.0


def is_available(item: dict) -> bool:
    """True when this inventory row can be cooked with right now.

    Two storage models share the table:
      item_type='quantity' — tracked in 份; available while quantity > 0
      item_type='boolean'  — 常备免记量 (葱, 鸡蛋…); available while in_stock
    A boolean row's `quantity` is NULL, so testing quantity alone silently makes
    every 常备 item look permanently stocked.
    """
    return (item.get("quantity") or 0) > 0 or bool(item.get("in_stock"))


def available_names(inv: dict) -> set:
    """Names of every ingredient currently in stock, across all five categories."""
    out: set = set()
    for items in inv.values():
        for item in items:
            if is_available(item):
                out.add(item["name"])
    return out
