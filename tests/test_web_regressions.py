"""Regression tests for bugs found in the web layer (portal, case page, admin API, verify lab)."""

import json
import re
from datetime import timedelta

from sqlalchemy import func, select

from app.db.models import CaseFile, Officer, RiskScore
from app.db.session import AsyncSessionLocal

ADMIN = {"X-API-Key": "admin-dev-key-12345"}


async def _create_company(client, name="PT Uji Baru", npp="NPP-UJI-001"):
    r = await client.post("/admin/company", headers=ADMIN, json={"name": name, "npp": npp})
    assert r.status_code == 200, r.text
    return r.json()["company_id"]


async def test_admin_company_create_works_and_checklist_is_structured(client):
    # Used to crash with NameError: timedelta/datetime were never imported in internal.py
    company_id = await _create_company(client)

    async with AsyncSessionLocal() as s:
        case = (await s.execute(select(CaseFile).where(CaseFile.company_id == company_id))).scalar_one()
    items = json.loads(case.checklist_json)
    assert items and all(isinstance(i, dict) and i["item"] for i in items)


async def test_portal_shows_one_tile_per_case_even_with_score_history(client):
    async with AsyncSessionLocal() as s:
        for sc in (await s.execute(select(RiskScore))).scalars().all():
            s.add(RiskScore(
                company_id=sc.company_id, as_of=sc.as_of - timedelta(days=7),
                rule_score=sc.rule_score, anomaly_score=sc.anomaly_score, risk=sc.risk,
                signals_json=sc.signals_json, est_low=sc.est_low, est_mid=sc.est_mid,
                est_high=sc.est_high, p_valid=sc.p_valid, model_version=sc.model_version,
            ))
        await s.commit()
        officers = (await s.execute(select(Officer))).scalars().all()

    for officer in officers:
        r = await client.get(f"/portal?officer_id={officer.officer_id}")  # may auto-sync new cases
        assert r.status_code == 200
        async with AsyncSessionLocal() as s:
            n = (await s.execute(
                select(func.count()).select_from(CaseFile).where(CaseFile.assigned_officer == officer.officer_id)
            )).scalar()
        assert len(re.findall(r'class="case-tile ', r.text)) == n


async def test_case_page_renders_legacy_string_checklist_and_missing_score(client):
    async with AsyncSessionLocal() as s:
        case = (await s.execute(select(CaseFile).limit(1))).scalar_one()
        case.checklist_json = json.dumps(["Item lama string A", "Item lama string B"])
        company_id, case_id = case.company_id, case.case_id
        for sc in (await s.execute(select(RiskScore).where(RiskScore.company_id == company_id))).scalars():
            await s.delete(sc)
        await s.commit()

    r = await client.get(f"/case/{case_id}")
    assert r.status_code == 200
    assert "Item lama string A" in r.text and "Item lama string B" in r.text
    assert "Belum dihitung" in r.text
    assert "Belum Dinilai" in r.text
    assert "Estimasi belum tersedia" in r.text


async def test_case_page_without_slip_does_not_invent_figures(client):
    company_id = await _create_company(client)
    async with AsyncSessionLocal() as s:
        case_id = (await s.execute(select(CaseFile.case_id).where(CaseFile.company_id == company_id))).scalar_one()

    r = await client.get(f"/case/{case_id}")
    assert r.status_code == 200
    assert "Belum ada slip pekerja" in r.text
    assert "Belum dapat dihitung" in r.text
    assert "Proporsi Kepatuhan Pelaporan Upah" not in r.text  # no bar without a slip to compare


async def test_portal_does_not_invent_estimate_for_unscored_case(client):
    async with AsyncSessionLocal() as s:
        case = (await s.execute(select(CaseFile).where(CaseFile.assigned_officer.is_not(None)).limit(1))).scalar_one()
        officer_id = case.assigned_officer
        for sc in (await s.execute(select(RiskScore).where(RiskScore.company_id == case.company_id))).scalars():
            await s.delete(sc)
        await s.commit()

    r = await client.get(f"/portal?officer_id={officer_id}")
    assert r.status_code == 200
    assert "12.500.000" not in r.text  # the old hard-coded placeholder estimate
    assert "Belum dihitung" in r.text


async def test_verify_lab_uses_rule_engine_without_api_key(client):
    page = await client.get("/verify")
    assert "internal-dev-key" not in page.text
    assert "alert(" not in page.text  # the fake "report saved" popup is gone

    # Modus #2 preset: salary 5.5jt on slip, deduction only 30rb, BPJS records UMK
    r = await client.post("/verify/evaluate", json={
        "base_wage": 5_000_000, "allowance": 500_000, "deduction": 30_000,
        "recorded_wage": 3_000_000, "umk": 3_300_000, "is_registered": True, "is_remitted": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert "DEDUCTION_LOW" in body["verdicts"]
    assert body["expected_deduction"] == 55_000

    # Compliant preset
    r = await client.post("/verify/evaluate", json={
        "base_wage": 4_500_000, "allowance": 500_000, "deduction": 50_000,
        "recorded_wage": 5_000_000, "umk": 3_300_000, "is_registered": True, "is_remitted": True,
    })
    assert r.json()["verdicts"] == ["OK"]


async def test_verify_evaluate_rejects_bad_input(client):
    r = await client.post("/verify/evaluate", json={"base_wage": -1, "deduction": 10})
    assert r.status_code == 422
    r = await client.post("/verify/evaluate", json={"base_wage": "abc", "deduction": 10})
    assert r.status_code == 422
