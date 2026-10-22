"""
Prediksi beban kerja antrean petugas (Pilar 4 — Horizon 3).

Rerata bergerak berbobot + faktor hari-kerja (weekday) — tanpa dependensi
pandas/numpy berat; cukup modul stdlib agar mudah diuji & deterministik.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RulesConfig
from app.db.models import CaseFile, Officer


def _history_sum(rows: list[tuple[date, float]], window: int) -> float:
    """Jumlah kasus per hari terakhir `window` hari (hanya hari berdata)."""
    day_counts = {day: count for day, count in rows}
    if not day_counts:
        return 0.0
    today = max(day_counts)
    return sum(day_counts.get(today - timedelta(days=offset), 0) for offset in range(window))


def _weekday_weight(target: date, weekday_weights: list[float]) -> float:
    index = target.weekday()
    if index < len(weekday_weights):
        return float(weekday_weights[index])
    return 1.0


async def forecast_workload(
    session: AsyncSession,
    as_of: Optional[date] = None,
    rules: Optional[RulesConfig] = None,
) -> dict:
    """
    Proyeksikan beban kasus `horizon_days` ke depan dari tabel case_file.

    Return {history: [{date, count}], forecast: [{date, expected_cases}], capacity: {...}}.
    """
    from app.config import get_rules

    rules = rules or get_rules()
    as_of = as_of or date.today()

    fc_cfg = rules.forecast
    horizon = int(fc_cfg["horizon_days"])
    window = int(fc_cfg["moving_average_window"])
    min_history = int(fc_cfg["min_history_days"])
    weekday_weights = [float(w) for w in fc_cfg["weekday_weights"]]

    start = as_of - timedelta(days=max(window - 1, 0))
    result = await _count_cases_by_day(session, start, as_of)
    history = [
        {"date": day.isoformat(), "count": count}
        for day, count in sorted(result)
    ]

    baseline = _history_sum(result, window) / max(window, 1)

    if len(history) < min_history or baseline <= 0:
        baseline = float(fc_cfg.get("fallback_daily_cases", 2))
        baseline_source = "fallback"
    else:
        baseline_source = "moving_average"

    capacity_hours = await _officer_daily_hours(session)
    visit_hours = float(rules.planner["default_visit_hours"])
    travel_hours = float(rules.planner["travel_hours"])
    per_visit = visit_hours + travel_hours
    daily_capacity = capacity_hours / per_visit if per_visit > 0 else 0.0

    forecast_rows: list[dict] = []
    cumulative = 0.0
    for offset in range(1, horizon + 1):
        target = as_of + timedelta(days=offset)
        weight = _weekday_weight(target, weekday_weights)
        expected = max(baseline * weight, 0.0)
        cumulative += expected
        forecast_rows.append(
            {
                "date": target.isoformat(),
                "expected_cases": round(expected, 2),
                "weekday": target.strftime("%A"),
            }
        )

    coverage = daily_capacity / baseline if baseline > 0 else None
    return {
        "as_of": as_of.isoformat(),
        "baseline_source": baseline_source,
        "baseline_cases_per_day": round(baseline, 3),
        "history": history,
        "forecast": forecast_rows,
        "expected_total": round(cumulative, 2),
        "capacity": {
            "daily_capacity_case": round(daily_capacity, 2),
            "total_capacity_hours": round(capacity_hours, 2),
            "hours_per_visit": per_visit,
        },
        "coverage_ratio": round(coverage, 3) if coverage is not None else None,
        "overloaded": coverage is not None and coverage < 1.0,
    }


async def _count_cases_by_day(
    session: AsyncSession, start: date, as_of: date
) -> list[tuple[date, float]]:
    result = await session.execute(
        select(func.date(CaseFile.created_at), func.count(CaseFile.case_id))
        .where(func.date(CaseFile.created_at) >= start)
        .where(func.date(CaseFile.created_at) <= as_of)
        .group_by(func.date(CaseFile.created_at))
    )
    rows = result.all()
    parsed: list[tuple[date, float]] = []
    for raw_day, count in rows:
        day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        parsed.append((day, float(count)))
    return parsed


async def _officer_daily_hours(session: AsyncSession) -> float:
    result = await session.execute(select(func.coalesce(func.sum(Officer.daily_hours), 0)))
    return float(result.scalar() or 0)
