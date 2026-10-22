"""Unit tests untuk batch feature builder (UMK dari Region + jendela lookback)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.db.models import (
    Billing,
    Company,
    CompanyFeatures,
    Enrollment,
    Region,
)
from app.risk.features import _period_cutoff, build_features


def _add_region_and_company(session) -> str:
    session.add(
        Region(
            region_id="r-test",
            province="Prov",
            city="Kota",
            umk=5_450_000,
            umk_year=2026,
        )
    )
    session.add(
        Company(
            company_id="c-test",
            npp="111-222",
            name="PT Unit Test",
            sector="jasa",
            region_id="r-test",
            size_bucket="small",
            est_headcount=10,
            registered_headcount=10,
            status="active",
        )
    )
    return "c-test"


async def test_umk_joins_from_region_not_hardcoded(db_session):
    company_id = _add_region_and_company(db_session)

    db_session.add(Enrollment(
        enrollment_id="e-1", company_id=company_id, worker_pid="W1",
        reported_wage_base=4_356_000, registered_status="karyawan_tetap",
    ))

    today = date.today()
    # Tiga bulan dalam jendela: lunas penuh
    for m in range(3):
        d = today - timedelta(days=30 * m)
        db_session.add(Billing(
            company_id=company_id, period=d.strftime("%Y-%m"),
            billed_amount=100_000, paid_amount=100_000, paid_at=d,
        ))
    # Tiga bulan luar jendela: tak dibayar — tak boleh ikut remit_ratio
    for m in range(3, 6):
        d = today - timedelta(days=30 * m)
        db_session.add(Billing(
            company_id=company_id, period=d.strftime("%Y-%m"),
            billed_amount=100_000, paid_amount=0, paid_at=None,
        ))

    n = await build_features(db_session, as_of=today)
    assert n == 1

    row = await db_session.get(CompanyFeatures, ("c-test", today))
    feat = json.loads(row.feature_json)

    expected_ratio = 4_356_000 / 5_450_001
    assert feat["umk"] == 5_450_000
    assert feat["avg_wage_ratio_to_umk"] == pytest.approx(expected_ratio, abs=1e-6)
    assert feat["remit_ratio"] == pytest.approx(1.0)
    assert feat["unpaid_months"] == 0

    assert "NaN" not in row.feature_json and "Infinity" not in row.feature_json


def test_period_cutoff_window_boundaries():
    cutoff_period, cutoff_date = _period_cutoff(date(2026, 10, 20), 3)
    assert (cutoff_period, cutoff_date) == ("2026-08", date(2026, 8, 1))

    cutoff_period, cutoff_date = _period_cutoff(date(2026, 2, 15), 2)
    assert (cutoff_period, cutoff_date) == ("2026-01", date(2026, 1, 1))

    cutoff_period, cutoff_date = _period_cutoff(date(2026, 3, 1), 12)
    assert cutoff_period == "2025-04" and cutoff_date == date(2025, 4, 1)


def test_period_cutoff_cross_year_backwards():
    cutoff_period, cutoff_date = _period_cutoff(date(2025, 1, 10), 3)
    assert cutoff_period == "2024-11" and cutoff_date == date(2024, 11, 1)
