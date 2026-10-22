"""Uji prediksi beban antrean: rerata bergerak, faktor hari kerja, kapasitas petugas."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from app.config import get_rules
from app.db.models import CaseFile, Company, Officer, Region
from app.queue.forecast import forecast_workload


@pytest.fixture()
def rules():
    return deepcopy(get_rules())


@pytest.mark.asyncio
async def test_forecast_empty_history_uses_fallback(db_session, rules):
    result = await forecast_workload(db_session, rules=rules)
    assert result["baseline_source"] == "fallback"
    assert result["baseline_cases_per_day"] == float(rules.forecast["fallback_daily_cases"])
    assert len(result["forecast"]) == int(rules.forecast["horizon_days"])


@pytest.mark.asyncio
async def test_forecast_moving_average_and_weekday_weights(db_session, rules):
    session = db_session
    session.add(Region(region_id="r-fc", province="P", city="C", umk=4_000_000, umk_year=2026))
    session.add(Officer(officer_id="o-fc", name="Bu Sari", region_id="r-fc", daily_hours=8.0))
    session.add(Company(
        company_id="c-fc", npp="9-c-fc", name="PT Forecast", sector="jasa",
        region_id="r-fc", size_bucket="small", est_headcount=10,
        registered_headcount=10, status="active",
    ))
    as_of = date.today()
    today_dt = datetime.combine(as_of, datetime.min.time()).replace(hour=9)
    day_batches = {
        1: 3,   # kemarin: 3 kasus
        2: 2,
        4: 3,
        6: 2,   # total 10 kasus dalam 14 hari
    }
    for offset, count in day_batches.items():
        for idx in range(count):
            session.add(CaseFile(
                case_id=f"case-fc-{offset}-{idx}", company_id="c-fc",
                status="assigned", priority=50.0,
                created_at=today_dt - timedelta(days=offset),
            ))
    await session.commit()

    result = await forecast_workload(session, as_of=as_of, rules=rules)
    window = int(rules.forecast["moving_average_window"])
    assert result["baseline_source"] == "moving_average"
    assert result["baseline_cases_per_day"] == pytest.approx(10 / window, abs=0.01)

    horizon = int(rules.forecast["horizon_days"])
    weights = [float(w) for w in rules.forecast["weekday_weights"]]
    friday = next(row for row in result["forecast"] if (as_of + timedelta(days=3)).isoformat() == row["date"])
    assert friday["expected_cases"] == pytest.approx(
        result["baseline_cases_per_day"] * weights[(as_of + timedelta(days=3)).weekday()],
        abs=0.01,
    )
    assert len(result["forecast"]) == horizon

    capacity = result["capacity"]
    visit_hours = float(rules.planner["default_visit_hours"])
    travel_hours = float(rules.planner["travel_hours"])
    assert capacity["daily_capacity_case"] == pytest.approx(8.0 / (visit_hours + travel_hours), abs=0.01)


@pytest.mark.asyncio
async def test_forecast_capacity_below_demand_flags_overloaded(db_session, rules):
    rules.forecast["fallback_daily_cases"] = 20
    result = await forecast_workload(db_session, rules=rules)
    assert result["overloaded"] is True
    assert result["coverage_ratio"] is not None and result["coverage_ratio"] < 1.0
