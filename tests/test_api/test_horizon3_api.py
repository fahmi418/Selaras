"""Tests API Horizon 3: cross-doc matching & forecast workload."""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.mark.asyncio
async def test_crossdoc_requires_key(client):
    missing = await client.post(
        "/api/company/c-none/crossdoc-match", json={"period": "2026-09"}
    )
    assert missing.status_code in (403, 422)


@pytest.mark.asyncio
async def test_crossdoc_unknown_company_404(client):
    settings = get_settings()
    res = await client.post(
        "/api/company/c-none/crossdoc-match",
        json={"period": "2026-09"},
        headers={"X-API-Key": settings.internal_api_key.get_secret_value()},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_crossdoc_roundtrip_critical_escalates(client):
    settings = get_settings()
    key = settings.internal_api_key.get_secret_value()
    admin_key = settings.admin_api_key.get_secret_value()

    created = await client.post(
        "/admin/company", json={"name": "PT CrossDoc", "npp": "7-7301"},
        headers={"X-API-Key": admin_key},
    )
    assert created.status_code == 200
    company_id = created.json()["company_id"]

    body = {
        "period": "2026-09",
        "pph21": {"period": "2026-09", "gross_wage": 8_000_000},
        "payroll": {
            "period": "2026-09",
            "rows": [{"gross_wage": 5_000_000, "jkn_deduction": 50_000}],
        },
        "bank_statement": {
            "period": "2026-09",
            "transfers": [{"amount": 4_000_000, "memo": "KPI"}],
        },
    }
    res = await client.post(
        f"/api/company/{company_id}/crossdoc-match", json=body, headers={"X-API-Key": key}
    )
    assert res.status_code == 200
    data = res.json()
    codes = {f["code"] for f in data["findings"]}
    assert {"X1_PPH21_PAYROLL", "X3_BANK_PAYROLL"} <= codes
    assert data["critical"] == 2
    assert data["escalate"] is True
    assert data["escalated"] is True

    from sqlalchemy import func, select

    from app.db.models import CaseFile
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        total = (await session.execute(
            select(func.count(CaseFile.case_id)).where(CaseFile.company_id == company_id)
        )).scalar_one()
    assert total >= 1


@pytest.mark.asyncio
async def test_crossdoc_minimum_bundle_reports_incomplete(client):
    settings = get_settings()
    key = settings.internal_api_key.get_secret_value()
    admin_key = settings.admin_api_key.get_secret_value()

    created = await client.post(
        "/admin/company", json={"name": "CrossDoc Tipis", "npp": "7-7302"},
        headers={"X-API-Key": admin_key},
    )
    company_id = created.json()["company_id"]

    res = await client.post(
        f"/api/company/{company_id}/crossdoc-match",
        json={"period": "2026-10"},
        headers={"X-API-Key": key},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["findings"][0]["code"] == "X0_INCOMPLETE"
    assert data["escalate"] is False


@pytest.mark.asyncio
async def test_forecast_requires_key(client):
    missing = await client.get("/api/forecast/workload")
    assert missing.status_code in (403, 422)


@pytest.mark.asyncio
async def test_forecast_roundtrip(client):
    settings = get_settings()
    res = await client.get(
        "/api/forecast/workload",
        headers={"X-API-Key": settings.internal_api_key.get_secret_value()},
    )
    assert res.status_code == 200
    data = res.json()
    for field in ("as_of", "baseline_source", "history", "forecast", "capacity", "expected_total"):
        assert field in data
    forecast_rules = data["forecast"]
    assert isinstance(forecast_rules, list) and forecast_rules
    first = forecast_rules[0]
    assert set(first) == {"date", "expected_cases", "weekday"}
    assert data["overloaded"] in (True, False)
