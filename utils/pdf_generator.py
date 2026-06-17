"""
Phase 8: Daily menu PDF generator.

Front page  — Prix Fixe menu card (date + recipe list + nutrition summary)
Back page   — 2×2 recipe execution cards (ingredients + steps per dish) + Mise en place footer

Output: ~/Downloads/menu_YYYYMMDD.pdf
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

# ── Chinese font setup ────────────────────────────────────────

_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

_CN_FONT  = "CNRegular"
_CN_BOLD  = "CNBold"
_FONT_REG: str = ""   # resolved at import time


def _register_fonts() -> None:
    global _FONT_REG
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont(_CN_FONT, path))
                pdfmetrics.registerFont(TTFont(_CN_BOLD,  path))
                _FONT_REG = path
                return
            except Exception:
                continue
    # Fallback: use built-in Helvetica (ASCII only — Chinese will be boxes)
    pdfmetrics.registerFont(pdfmetrics.getFont("Helvetica"))
    _FONT_REG = "fallback"


_register_fonts()

# ── Constants ─────────────────────────────────────────────────

W, H = letter          # 612 × 792 pt
MARGIN = 0.75 * inch
INNER_W = W - 2 * MARGIN
GRID_W  = W / 2
GRID_H  = H / 2

_GRAY   = colors.HexColor("#888888")
_DARK   = colors.HexColor("#1a1a1a")
_ACCENT = colors.HexColor("#8B6914")   # warm gold — Prix Fixe feel
_RULE   = colors.HexColor("#cccccc")

_WEEKDAYS_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ── Helpers ───────────────────────────────────────────────────

def _cn(c: canvas.Canvas, text: str, x: float, y: float,
        size: int = 11, bold: bool = False, color=_DARK, align: str = "left") -> None:
    c.setFont(_CN_BOLD if bold else _CN_FONT, size)
    c.setFillColor(color)
    if align == "center":
        c.drawCentredString(x, y, text)
    elif align == "right":
        c.drawRightString(x, y, text)
    else:
        c.drawString(x, y, text)


def _hline(c: canvas.Canvas, x1: float, x2: float, y: float,
           width: float = 0.5, color=_RULE) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)


def _wrap_text(text: str, max_chars: int = 22) -> list:
    """CJK line wrapper — every character counts as 1 unit."""
    words = list(text)
    lines, cur = [], []
    for ch in words:
        cur.append(ch)
        if len(cur) >= max_chars:
            lines.append("".join(cur))
            cur = []
    if cur:
        lines.append("".join(cur))
    return lines or [""]


def _wrap_en(text: str, max_chars: int = 72) -> list:
    """Word-boundary wrapper for English text."""
    words = text.split()
    lines, cur, length = [], [], 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if length + add > max_chars and cur:
            lines.append(" ".join(cur))
            cur, length = [w], len(w)
        else:
            cur.append(w)
            length += add
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


def _get_daily_ingredients(all_ingredients: dict) -> tuple:
    """Split deduplicated daily ingredients into (mains, condiments)."""
    mains, conds = set(), set()
    for ings in all_ingredients.values():
        for ing in ings:
            name = ing.get("name")
            if not name:
                continue
            if ing.get("is_condiment"):
                conds.add(name)
            else:
                mains.add(name)
    return sorted(mains), sorted(conds)


def _draw_mise_en_place(
    c: canvas.Canvas,
    mains: list,
    conds: list,
    footer_h: float,
) -> None:
    """Draw the 'Mise en place' summary — two sections: 主料 + 调料."""
    if not mains and not conds:
        return

    y = footer_h - 0.22 * inch

    # Decorative separator line
    _hline(c, MARGIN, W - MARGIN, footer_h, width=1.0, color=_ACCENT)

    # Title
    _cn(c, "【备料总览 / Mise en place】", MARGIN, y, size=10, bold=True, color=_ACCENT)
    y -= 0.20 * inch

    def _section(label: str, items: list, y: float) -> float:
        if not items:
            return y
        _cn(c, label, MARGIN, y, size=8, color=_GRAY)
        y -= 0.16 * inch
        text = "、".join(items)
        for ln in _wrap_text(text, max_chars=90):
            _cn(c, ln, MARGIN, y, size=9, color=_DARK)
            y -= 0.16 * inch
        return y - 0.04 * inch  # tiny gap between sections

    y = _section("主料 / Main", mains, y)
    y = _section("调料 / Condiments", conds, y)


# ── Front page ────────────────────────────────────────────────

def _front_page(
    c: canvas.Canvas,
    date: datetime,
    recipes: list,
    nutrition: dict,
) -> None:
    """Prix Fixe restaurant-style menu front page."""

    cx = W / 2

    # ── Header ────────────────────────────────────────────────
    y = H - MARGIN - 0.3 * inch

    weekday_zh = _WEEKDAYS_ZH[date.weekday()]
    weekday_en = _WEEKDAYS_EN[date.weekday()]
    _cn(c, f"{date.year}年{date.month}月{date.day}日  {weekday_zh}", cx, y,
        size=13, bold=True, color=_ACCENT, align="center")
    y -= 0.22 * inch
    _cn(c, f"{weekday_en}, {date.strftime('%B %-d, %Y')}", cx, y,
        size=9, color=_GRAY, align="center")

    y -= 0.30 * inch
    _hline(c, MARGIN, W - MARGIN, y, width=1.5, color=_ACCENT)
    y -= 0.22 * inch
    _cn(c, "Tonight's Menu", cx, y, size=10, color=_GRAY, align="center")
    y -= 0.10 * inch
    _hline(c, MARGIN, W - MARGIN, y, width=0.5, color=_RULE)

    # ── Recipe list — restaurant menu style ───────────────────
    y -= 0.42 * inch
    x_bullet = MARGIN
    x_text   = MARGIN + 0.28 * inch

    for r in recipes:
        zh_name  = r.get("name", "")
        en_name  = (r.get("en_name") or "").strip()
        en_desc  = (r.get("en_desc") or "").strip()
        zh_desc  = (r.get("zh_desc") or "").strip()

        # ◆ Chinese name
        _cn(c, "◆", x_bullet, y, size=11, bold=True, color=_ACCENT)
        _cn(c, zh_name, x_text, y, size=15, bold=True)
        y -= 0.27 * inch

        # English name (italicised via slightly smaller weight)
        if en_name:
            _cn(c, en_name, x_text, y, size=10, color=_DARK)
            y -= 0.20 * inch

        # Description line(s) — wrap at ~68 chars
        desc = en_desc or zh_desc
        if desc:
            desc_lines = _wrap_en(desc, max_chars=68)[:2]  # cap at 2 lines
            for ln in desc_lines:
                _cn(c, ln, x_text + 0.05 * inch, y, size=9, color=_GRAY)
                y -= 0.175 * inch
        elif not en_name:
            pass  # no sub-lines at all → less gap

        y -= 0.28 * inch   # breathing room between entries

    # ── Nutrition block — drawn bottom-up, truly pinned to footer ─
    bfst_kcal   = nutrition.get("breakfast_kcal", 0) + nutrition.get("lunch_kcal", 0)
    bfst_prot   = nutrition.get("breakfast_protein", 0) + nutrition.get("lunch_protein", 0)
    dinner_kcal = nutrition.get("dinner_kcal", 0)
    dinner_prot = nutrition.get("dinner_protein", 0)
    total_kcal  = nutrition.get("total_kcal", 0)
    total_prot  = nutrition.get("total_protein", 0)
    total_fat   = nutrition.get("total_fat", 0)
    total_carb  = nutrition.get("total_carbs", 0)
    total_na    = nutrition.get("total_sodium", 0)
    total_fiber = nutrition.get("total_fiber", 0)
    total_ca    = nutrition.get("total_calcium", 0)
    total_fe    = nutrition.get("total_iron", 0)
    total_vc    = nutrition.get("total_vitc", 0)
    total_k     = nutrition.get("total_potassium", 0)
    total_vd    = nutrition.get("total_vitd", 0)
    total_va    = nutrition.get("total_vita", 0)
    total_mg    = nutrition.get("total_magnesium", 0)
    total_zn    = nutrition.get("total_zinc", 0)

    # Start just above the footer line and build upward
    yn = MARGIN * 0.7 + 0.14 * inch

    # Row: micronutrients — line 2 (维D / 维A / 镁 / 锌)
    _cn(c,
        f"维D {total_vd:.1f}µg  ·  维A {total_va:.0f}µg  ·  镁 {total_mg:.0f}mg  ·  锌 {total_zn:.1f}mg",
        cx, yn, size=9, color=_GRAY, align="center")
    yn += 0.20 * inch

    # Row: micronutrients — line 1 (钙 / 铁 / 维C / 钾)
    _cn(c,
        f"钙 {total_ca:.0f}mg  ·  铁 {total_fe:.1f}mg  ·  维C {total_vc:.0f}mg  ·  钾 {total_k:.0f}mg",
        cx, yn, size=9, color=_GRAY, align="center")
    yn += 0.20 * inch

    # Row: sodium / fiber
    na_warn  = " ⚠" if total_na    > 2300 else ""
    fiber_ok = " ✓" if total_fiber >= 20  else ""
    _cn(c,
        f"钠 {total_na:.0f}mg{na_warn}  ·  膳食纤维 {total_fiber:.1f}g{fiber_ok}",
        cx, yn, size=9, color=_GRAY, align="center")
    yn += 0.22 * inch

    # Row: full macros breakdown (fat / carbs as gray secondary line)
    _cn(c,
        f"脂肪 {total_fat:.0f}g  ·  碳水 {total_carb:.0f}g",
        cx, yn, size=9, color=_GRAY, align="center")
    yn += 0.26 * inch

    # Row: total (bold) — kcal + protein headline numbers
    _cn(c, "全日合计",                   MARGIN + 0.1*inch,  yn, size=11, bold=True)
    _cn(c, f"{total_kcal:.0f} kcal",    MARGIN + 2.2*inch,  yn, size=11, bold=True)
    _cn(c, f"蛋白质  {total_prot:.0f}g", MARGIN + 3.5*inch, yn, size=11, bold=True)
    yn += 0.4 * inch

    # Inner separator
    _hline(c, MARGIN + 0.1*inch, W - MARGIN - 0.1*inch, yn)
    yn += 0.26 * inch

    # Meal rows (dinner, then bfst+lunch above it)
    def _meal_row(label: str, kcal: float, protein: float, yy: float) -> float:
        _cn(c, label,                MARGIN + 0.1*inch,  yy, size=10)
        _cn(c, f"{kcal:.0f} kcal",  MARGIN + 2.2*inch,  yy, size=10)
        _cn(c, f"蛋白质  {protein:.0f}g", MARGIN + 3.5*inch, yy, size=10)
        return yy + 0.26 * inch

    yn = _meal_row("晚　　　　餐", dinner_kcal, dinner_prot, yn)
    yn = _meal_row("早餐 + 午餐", bfst_kcal,   bfst_prot,   yn)

    # Heading
    _cn(c, "今日营养摘要  /  Daily Nutrition",
        cx, yn, size=9, color=_GRAY, align="center")
    yn += 0.22 * inch

    # Top separator rule
    _hline(c, MARGIN, W - MARGIN, yn, width=0.5, color=_RULE)

    # ── Footer ────────────────────────────────────────────────
    _hline(c, MARGIN, W - MARGIN, MARGIN * 0.7, width=0.5, color=_RULE)
    _cn(c, "Home Kitchen  ·  Made with 小喵皇",
        cx, MARGIN * 0.4, size=7, color=_GRAY, align="center")


# ── Back page ─────────────────────────────────────────────────

def _recipe_card(
    c: canvas.Canvas,
    r: dict,
    ingredients: list,
    x0: float, y0: float,
    cell_w: float, cell_h: float
) -> None:
    """Draw one recipe card in a dynamically sized grid cell."""
    pad = 0.12 * inch
    x = x0 + pad
    y = y0 + cell_h - pad

    # Title bar
    c.setFillColor(_ACCENT)
    c.rect(x0, y0 + cell_h - 0.38 * inch, cell_w, 0.38 * inch, fill=1, stroke=0)
    _cn(c, r.get("name", ""), x, y0 + cell_h - 0.26 * inch, size=11, bold=True,
        color=colors.white)

    y -= 0.38 * inch
    y -= 0.1 * inch   # breathing room between title bar and content

    # Ingredients
    _cn(c, "食材", x, y, size=10, bold=True, color=_ACCENT)
    y -= 0.2 * inch

    main_ings = [i for i in ingredients if not i.get("is_condiment")]
    cond_ings = [i for i in ingredients if i.get("is_condiment")]

    for ing in main_ings[:8]:
        amt  = ing.get("amount") or 0
        unit = ing.get("unit", "g")
        name = ing.get("name", "")
        line = f"{name}  {amt:g}{unit}" if amt else name
        _cn(c, f"• {line}", x, y, size=9)
        y -= 0.185 * inch

    if cond_ings:
        cond_names = "、".join(i.get("name", "") for i in cond_ings[:5])
        _cn(c, f"调味：{cond_names}", x, y, size=8, color=_GRAY)
        y -= 0.185 * inch

    y -= 0.06 * inch
    _hline(c, x, x0 + cell_w - 0.12 * inch, y)
    y -= 0.14 * inch

    # Steps
    steps = r.get("steps") or []
    if steps:
        _cn(c, "步骤", x, y, size=10, bold=True, color=_ACCENT)
        y -= 0.2 * inch
        for i, step in enumerate(steps, 1):
            if y < y0 + 0.25 * inch:
                _cn(c, "…", x, y, size=8, color=_GRAY)
                break
            prefix = f"{i}. "
            lines = _wrap_text(prefix + step, max_chars=30)
            for j, ln in enumerate(lines):
                if y < y0 + 0.2 * inch:
                    break
                _cn(c, ("   " + ln if j > 0 else ln), x, y, size=9)
                y -= 0.175 * inch
    else:
        _cn(c, "（暂无步骤）", x, y, size=9, color=_GRAY)


def _nutrition_card(c: canvas.Canvas, nutrition: dict, x0: float, y0: float, cell_w: float, cell_h: float) -> None:
    """Filler card showing nutrition summary when recipe count < 4."""
    pad = 0.18 * inch
    x = x0 + pad

    c.setFillColor(_ACCENT)
    c.rect(x0, y0 + cell_h - 0.38 * inch, cell_w, 0.38 * inch, fill=1, stroke=0)
    _cn(c, "今日营养小结", x, y0 + cell_h - 0.26 * inch, size=11, bold=True, color=colors.white)

    y = y0 + cell_h - 0.38 * inch - pad
    rows = [
        ("总热量",   f"{nutrition.get('total_kcal', 0):.0f} kcal"),
        ("蛋白质",   f"{nutrition.get('total_protein', 0):.0f} g"),
        ("脂肪",     f"{nutrition.get('total_fat', 0):.0f} g"),
        ("碳水",     f"{nutrition.get('total_carbs', 0):.0f} g"),
        ("钠",       f"{nutrition.get('total_sodium', 0):.0f} mg"),
        ("膳食纤维", f"{nutrition.get('total_fiber', 0):.1f} g"),
    ]
    for label, val in rows:
        _cn(c, label, x, y, size=9)
        _cn(c, val, x0 + cell_w - pad, y, size=9, align="right")
        y -= 0.22 * inch


def _back_page(
    c: canvas.Canvas,
    recipes: list,
    all_ingredients: dict,  # recipe_id → list of ingredient dicts
    nutrition: dict,
) -> None:
    """4-grid recipe card back page with Condiment Footer."""
    
    # 1. Prepare Mise en place & Dimensions
    mains, conds = _get_daily_ingredients(all_ingredients)
    # Reserve more vertical space now that we render two sections
    footer_h = 1.5 * inch if (mains or conds) else 0
    
    cell_w = W / 2
    cell_h = (H - footer_h) / 2
    
    # 2. Grid cell origins: (x0, y0, index)
    cells = [
        (0,      footer_h + cell_h, 0),    # top-left
        (cell_w, footer_h + cell_h, 1),    # top-right
        (0,      footer_h,          2),    # bottom-left
        (cell_w, footer_h,          3),    # bottom-right
    ]

    # 3. Draw grid lines (adjusted for footer)
    c.setStrokeColor(_RULE)
    c.setLineWidth(0.5)
    c.line(cell_w, footer_h, cell_w, H)                 # vertical
    c.line(0, footer_h + cell_h, W, footer_h + cell_h)  # horizontal

    # 4. Draw Cards
    for idx, (x0, y0, _) in enumerate(cells):
        if idx < len(recipes):
            r = recipes[idx]
            ings = all_ingredients.get(r["id"], [])
            _recipe_card(c, r, ings, x0, y0, cell_w, cell_h)
        else:
            if idx == len(recipes):
                _nutrition_card(c, nutrition, x0, y0, cell_w, cell_h)

    # 5. Draw Footer
    _draw_mise_en_place(c, mains, conds, footer_h)


# ── Public entry point ────────────────────────────────────────

def generate_daily_menu_pdf(
    recipes: list,
    all_ingredients: dict,
    nutrition: dict,
    date: datetime = None,
) -> str:
    """
    Generate a 2-page PDF and return the output path.

    recipes          — list of recipe dicts (from db.recipes)
    all_ingredients  — {recipe_id: [ingredient dicts]} for each recipe
    nutrition        — keys: breakfast_kcal, breakfast_protein, lunch_kcal, lunch_protein,
                             dinner_kcal, dinner_protein, total_kcal, total_protein,
                             total_fat, total_carbs, total_sodium, total_fiber
    """
    if date is None:
        date = datetime.now()

    out_path = Path.home() / "Downloads" / f"menu_{date.strftime('%Y%m%d')}.pdf"
    c = canvas.Canvas(str(out_path), pagesize=letter)

    # ── Page 1: front ─────────────────────────────────────────
    _front_page(c, date, recipes, nutrition)
    c.showPage()

    # ── Page 2: back ──────────────────────────────────────────
    _back_page(c, recipes, all_ingredients, nutrition)
    c.save()

    return str(out_path)


def open_pdf(path: str) -> None:
    """Open the generated PDF with the system default viewer."""
    try:
        subprocess.Popen(["open", path])
    except Exception:
        pass