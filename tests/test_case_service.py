"""Tests service kasus terpusat: reuse, token hash, prioritas, tenggat."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.cases.service import (
    baseline_priority,
    boosted_priority,
    find_open_case,
    issue_access_token,
    priority_floor_report,
    scale_priority,
    upsert_active_case,
)
from app.config import get_rules
from app.db.models import CaseFile

REASONS = [{"title": "Laporan Pekerja", "evidence": "1 laporan", "source": "bot"}]
CHECKLIST = [{"item": "Minta daftar pekerja", "checked": False}]


@pytest.mark.asyncio
async def test_upsert_creates_assigned_case_with_hashed_token(db_session):
    case = await upsert_active_case(
        db_session, company_id="c-1", reasons=REASONS, checklist=CHECKLIST, priority=9876.0
    )

    assert case.case_id.startswith("case-")
    assert case.status == "assigned"
    assert case.priority == 9876.0
    assert case.assigned_officer is None
    assert case.access_token_hash                     # token disimpan sbg hash
    assert len(case.access_token_hash) == 64
    assert case.token_expires is not None
    assert case.reasons_json[0] != "{" or "Laporan Pekerja" in case.reasons_json


@pytest.mark.asyncio
async def test_upsert_reuses_open_case_and_prepends_reasons(db_session):
    first = await upsert_active_case(
        db_session, company_id="c-1", reasons=list(REASONS), checklist=CHECKLIST, priority=1000.0
    )

    new_reasons = [{"title": "Selisih Setoran", "evidence": "x", "source": "y"}]
    second = await upsert_active_case(
        db_session,
        company_id="c-1",
        reasons=new_reasons,
        checklist=CHECKLIST,
        priority=2000.0,
        due_days=3,
        prepend_reasons=True,
    )

    assert second.case_id == first.case_id
    count = (await db_session.execute(select(func.count()).select_from(CaseFile))).scalar_one()
    assert count == 1

    from json import loads

    reasons = loads(second.reasons_json)
    assert reasons[0]["title"] == "Selisih Setoran"
    assert reasons[1]["title"] == "Laporan Pekerja"
    assert second.priority == 2000.0
    import datetime as dt

    assert second.due_date == dt.date.today() + dt.timedelta(days=3)


@pytest.mark.asyncio
async def test_upsert_without_prepend_replaces_reasons(db_session):
    await upsert_active_case(
        db_session, company_id="c-2", reasons=list(REASONS), checklist=CHECKLIST, priority=10.0
    )
    case = await upsert_active_case(
        db_session, company_id="c-2", reasons=[{"title": "Baru", "evidence": "", "source": ""}],
        checklist=CHECKLIST, priority=20.0,
    )
    from json import loads

    assert len(loads(case.reasons_json)) == 1


@pytest.mark.asyncio
async def test_issue_access_token_hashes_raw_token(db_session):
    case = CaseFile(case_id="case-tokentest", company_id="c-3")
    db_session.add(case)

    token = await issue_access_token(case)

    from hashlib import sha256

    assert token
    assert sha256(token.encode()).hexdigest() == case.access_token_hash
    assert token != case.access_token_hash


def test_priority_helpers_from_rules():
    rules = get_rules()
    scale = float(rules.cases["priority_scale"])
    assert priority_floor_report(rules) == float(rules.cases["report_boost_risk_points"]) * scale
    assert baseline_priority(rules) == float(rules.cases["baseline_risk_points"]) * scale
    assert scale_priority(80.0, rules) == pytest.approx(80.0 * scale)
    assert boosted_priority(None) == pytest.approx(priority_floor_report(rules))
    assert boosted_priority(10_500.0) == pytest.approx(10_500.0)


@pytest.mark.asyncio
async def test_find_open_case_latest_and_skips_closed(db_session):
    await upsert_active_case(db_session, company_id="c-4", reasons=REASONS, checklist=CHECKLIST, priority=1.0)
    first = await find_open_case(db_session, "c-4")
    assert first is not None

    first.status = "closed"
    assert await find_open_case(db_session, "c-4") is None
