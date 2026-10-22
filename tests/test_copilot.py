"""Tests copilot Fase 1: briefing pra-audit & Voice-to-BAP (provider di belakang antarmuka)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from app.config import get_rules
from app.copilot.bap import (
    BAPDraft,
    BAPGenerationError,
    BAPNotConfigured,
    DemoScriptedBAPProvider,
    generate_bap,
    resolve_bap_provider,
)
from app.copilot.briefing import (
    build_briefing,
    format_briefing_text,
    top_prescriptive_actions,
)
from app.db.models import Billing, CaseFile, Company, Region, RiskScore


def _seed_case(session) -> CaseFile:
    session.add(Region(region_id="r1", province="P", city="C", umk=4_000_000, umk_year=2026))
    session.add(Company(
        company_id="c1", npp="1-1", name="PT Wasrik Sejahtera", sector="jasa",
        region_id="r1", size_bucket="small",
        est_headcount=20, registered_headcount=14, status="active",
    ))
    session.add(RiskScore(
        company_id="c1", as_of=date.today(), rule_score=0.8, anomaly_score=0.6,
        risk=84.0, p_valid=0.84, est_mid=9_500_000, est_low=6_000_000, est_high=12_000_000,
        signals_json="{\"C1_under_reporting\": 0.7, \"C3_remit_inconsistent\": 0.5, "
                     "\"C2_headcount_gap\": 0.1}",
        model_version="rule+isoforest-v1",
    ))
    months = [date.today().strftime("%Y-%m")]
    y, m = date.today().year, date.today().month - 1
    if m == 0:
        y, m = y - 1, 12
    months.append(f"{y:04d}-{m:02d}")
    for period, paid in zip(months, (0, 95_000)):
        session.add(Billing(
            company_id="c1", period=period, billed_amount=100_000, paid_amount=paid,
            paid_at=date.today() if paid else None,
        ))
    return CaseFile(
        case_id="case-brief", company_id="c1", status="assigned",
        priority=8400.0, due_date=date.today(),
        reasons_json="[{'code': 'AUTO_SYNC'}]".replace("'", '"'),
        checklist_json="[{\"item\": \"Minta payroll\", \"checked\": false}]",
    )


@pytest.mark.asyncio
async def test_build_briefing_sets_policy_and_actions(db_session):
    session = db_session
    case = _seed_case(session)
    session.add(case)
    await session.flush()

    briefing = await build_briefing(session, case)

    assert briefing["company"]["name"] == "PT Wasrik Sejahtera"
    assert briefing["risk"]["risk"] == pytest.approx(84.0)
    assert briefing["risk"]["signals"]["C1_under_reporting"] == pytest.approx(0.7)
    assert len(briefing["billing_history"]) == 2
    assert briefing["worker_reports"]["lookback_months"] == 6

    actions = briefing["prescriptive_actions"]
    assert actions[0]["signal"] == "C1_under_reporting"      # kontribusi tertinggi
    assert "payroll asli" in actions[0]["action"]
    assert all(a["contribution"] > a.get("floor", 0.1) or a["contribution"] > 0.0 for a in actions)
    assert briefing["checklist_pending"][0]["item"] == "Minta payroll"

    text = format_briefing_text(briefing)
    assert "Briefing Pra-Audit" in text and "PT Wasrik Sejahtera" in text and "Strategi wawancara" in text


def test_actions_respect_rules_catalog_and_floor():
    rules = deepcopy(get_rules())
    signals = {"C1_under_reporting": 0.9, "C999_unknown": 1.0, "C3_remit_inconsistent": 0.05}
    actions = top_prescriptive_actions(signals, rules)

    keys = [a["signal"] for a in actions]
    assert "C1_under_reporting" in keys
    assert all(k in rules.briefing["prescriptive_actions"] for k in keys)
    assert len(actions) <= int(rules.briefing["interview_goals_max"])
    assert all(a["contribution"] > float(rules.thresholds["reason_contribution"]) for a in actions)


@pytest.mark.asyncio
async def test_briefing_missing_company_returns_none(db_session):
    case = CaseFile(case_id="case-orphan", company_id="c-tak-ada")
    db_session.add(case)
    await db_session.flush()

    assert await build_briefing(db_session, case) is None


def test_demo_provider_direct_compose():
    provider = DemoScriptedBAPProvider()
    import asyncio

    raw = asyncio.run(provider.compose(
        "P: Jumlah pekerja?\nJ: 42 orang.\nP: Bukti setor?\n",
        "PT Demo",
        "case-x",
    ))

    assert raw["summary"].startswith("Draf BAP untuk PT Demo")
    assert raw["points"][0] == {"question": "Jumlah pekerja?", "answer": "42 orang."}
    assert raw["points"][1] == {"question": "Bukti setor?", "answer": "(tidak dijawab)"}


def test_demo_provider_falls_back_to_single_summary_point():
    provider = DemoScriptedBAPProvider()
    import asyncio

    raw = asyncio.run(provider.compose("perusahaan tak kooperatif.", "PT X", "c-1"))
    assert raw["points"][0]["question"] == "Ringkasan temuan pemeriksaan"
    assert raw["points"][0]["answer"] == "perusahaan tak kooperatif."


@pytest.mark.asyncio
async def test_resolve_provider_rules_off(db_session):
    rules = deepcopy(get_rules())
    rules.briefing["transcript_provider"] = "off"
    with pytest.raises(BAPNotConfigured):
        resolve_bap_provider(rules)


@pytest.mark.asyncio
async def test_generate_bap_demo_success(db_session):
    get_settings_mode_default = get_rules()
    draft = await generate_bap(
        case_id="case-demo",
        company_name="PT Sejahtera",
        transcript="P: Jumlah pekerja?\nJ: 20 orang.\n",
    )

    assert isinstance(draft, BAPDraft)
    assert draft.case_id == "case-demo"
    assert draft.provider == "demo-scripted"
    assert draft.points[0].answer == "20 orang."
    assert draft.transcript.startswith("P:")
    assert draft.summary

    # Konfigurasi rules harus tetap utuh setelah panggilan.
    assert get_settings_mode_default.briefing["transcript_provider"] == "auto"


@pytest.mark.asyncio
async def test_generate_bap_rejects_empty_transcript():
    with pytest.raises(BAPGenerationError):
        await generate_bap(case_id="c", company_name="x", transcript="   ")
