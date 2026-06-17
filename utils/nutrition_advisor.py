"""Phase 6: Gemini-powered daily nutrition advisor with 3-calls/day rate limit."""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from db.init_db import get_connection

_DAILY_LIMIT   = 3
_SETTING_KEY   = "advisor_calls"   # JSON: {"date": "YYYY-MM-DD", "count": N}
_MODEL         = "gemini-2.5-flash"


# ── Rate-limit helpers ─────────────────────────────────────────

def _load_call_record() -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE key=?", (_SETTING_KEY,)
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    finally:
        conn.close()
    return {"date": "", "count": 0}


def _save_call_record(rec: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?,?)",
            (_SETTING_KEY, json.dumps(rec)),
        )
        conn.commit()
    finally:
        conn.close()


def calls_remaining() -> int:
    rec   = _load_call_record()
    today = str(date.today())
    if rec.get("date") != today:
        return _DAILY_LIMIT
    return max(0, _DAILY_LIMIT - rec.get("count", 0))


def _increment_calls() -> None:
    rec   = _load_call_record()
    today = str(date.today())
    if rec.get("date") != today:
        rec = {"date": today, "count": 0}
    rec["count"] = rec.get("count", 0) + 1
    _save_call_record(rec)


# ── Prompt builder ─────────────────────────────────────────────

def _build_prompt(total: dict, dinner_names: list[str], dri: dict) -> str:
    def pct(val, ref):
        return round(val / ref * 100) if ref else 0

    dinner_str = "、".join(dinner_names) if dinner_names else "（未规划）"

    return f"""你是一位专业营养师，请根据以下今日饮食数据给出简洁、实用的中文建议（3-5条，每条1-2句话）。

【今日晚餐菜单】{dinner_str}

【全日营养摄入（每人）】
- 热量：{total.get('kcal', 0):.0f} kcal（DRI达成率 {pct(total.get('kcal',0), dri.get('kcal',1))}%）
- 蛋白质：{total.get('protein', 0):.1f} g（{pct(total.get('protein',0), dri.get('protein',1))}%）
- 脂肪：{total.get('fat', 0):.1f} g（{pct(total.get('fat',0), dri.get('fat',1))}%）
- 碳水：{total.get('carbs', 0):.1f} g（{pct(total.get('carbs',0), dri.get('carbs',1))}%）
- 钠：{total.get('sodium', 0):.0f} mg（{pct(total.get('sodium',0), dri.get('sodium',1))}%）
- 膳食纤维：{total.get('fiber', 0):.1f} g（{pct(total.get('fiber',0), dri.get('fiber',1))}%）
- 维生素C：{total.get('vitc', 0):.1f} mg（{pct(total.get('vitc',0), dri.get('vitc',1))}%）
- 钙：{total.get('calcium', 0):.0f} mg（{pct(total.get('calcium',0), dri.get('calcium',1))}%）
- 铁：{total.get('iron', 0):.1f} mg（{pct(total.get('iron',0), dri.get('iron',1))}%）

请重点关注明显不足或过量的营养素，以及晚餐菜单的搭配合理性。回答请直接列出建议，不要前言。"""


# ── Main API call ──────────────────────────────────────────────

def get_nutrition_advice(total: dict, dinner_names: list[str], dri: dict) -> Optional[str]:
    """Call Gemini and return advice text. Returns None if rate-limited or error."""
    if calls_remaining() <= 0:
        return None

    try:
        from google import genai
        import os
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        client = genai.Client(api_key=api_key)
        prompt = _build_prompt(total, dinner_names, dri)
        resp   = client.models.generate_content(model=_MODEL, contents=prompt)
        text   = resp.text.strip()
        _increment_calls()
        return text
    except Exception as e:
        return f"[AI建议获取失败: {e}]"
