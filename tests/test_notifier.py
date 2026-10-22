"""Tests notifier pagi: limit dari config, fallback skor terakhir, risk tag."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from json import dumps

import pytest

from app.config import get_rules
from app.db.models import CaseFile, Company, Officer, RiskScore, Region
from app.queue import notifier as notifier_mod
from app.queue.notifier import risk_level, send_morning_messages


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((str(chat_id), text))


def _seed_officer_company_scores(session) -> tuple[str, str]:
    session.add(Region(region_id="r1", province="P", city="C", umk=4_000_000, umk_year=2026))
    session.add(Officer(
        officer_id="o1", name="Bu Sari", region_id="r1", daily_hours=8,
        telegram_chat_id="777",
    ))
    for company_id, name, risk in (
        ("cA", "PT Amanah", 90.0),
        ("cB", "PT Berkah", 50.0),
    ):
        session.add(Company(
            company_id=company_id, npp=f"9-{company_id}", name=name, sector="jasa",
            region_id="r1", size_bucket="small",
            est_headcount=10, registered_headcount=10, status="active",
        ))
        session.add(RiskScore(
            company_id=company_id, as_of=date.today(), rule_score=risk / 100, anomaly_score=0.0,
            risk=risk, p_valid=risk / 100, est_mid=1_000_000, est_low=800_000, est_high=1_200_000,
            signals_json="{}", model_version="rule+isoforest-v1",
        ))
    return ("cA", "cB")


def _add_case(session, company_id: str, priority: float, due_date: date) -> CaseFile:
    case = CaseFile(
        case_id=f"case-{company_id.lower()}", company_id=company_id,
        status="assigned", assigned_officer="o1", priority=priority,
        due_date=due_date,
        reasons_json=dumps([{"title": "Setoran Tidak Konsisten"}], ensure_ascii=False),
        checklist_json="[]",
    )
    session.add(case)
    return case


@pytest.mark.asyncio
async def test_limit_and_labels_follow_config(db_session):
    session = db_session
    _seed_officer_company_scores(session)
    _add_case(session, "cA", priority=9000.0, due_date=date.today())
    _add_case(session, "cB", priority=5000.0, due_date=date.today())

    rules = deepcopy(get_rules())
    rules.morning_message["max_cases_in_message"] = 1
    rules.risk_labels = {"high": 70, "medium": 40, "low": 0}
    original_get_rules = notifier_mod.get_rules
    notifier_mod.get_rules = lambda: rules
    try:
        bot = FakeBot()
        sent = await send_morning_messages(session, date.today(), bot)
    finally:
        notifier_mod.get_rules = original_get_rules

    assert sent == 1
    chat_id, text = bot.sent[0]
    assert chat_id == "777"
    assert "Bu Sari" in text and "Selamat pagi" in text
    assert text.count("PT Amanah") == 1          # prioritas tertinggi
    assert "PT Berkah" not in text               # limit 1 kasus dari morning_message
    assert "[TINGGI]" in text


@pytest.mark.asyncio
async def test_falls_back_to_latest_available_score(db_session):
    session = db_session
    _seed_officer_company_scores(session)
    _add_case(session, "cA", priority=9000.0, due_date=date.today())
    _add_case(session, "cB", priority=5000.0, due_date=date.today())

    from sqlalchemy import delete

    # Hapus semua skor hari ini, sisakan skor lama cA — fallback harus tetap jalan.
    await session.execute(delete(RiskScore).where(RiskScore.as_of == date.today()))
    session.add(RiskScore(
        company_id="cA", as_of=date.today() - timedelta(days=29),
        rule_score=0.9, anomaly_score=0.0, risk=90.0, p_valid=0.9,
        est_mid=1_000_000, est_low=800_000, est_high=1_200_000,
        signals_json="{}", model_version="rule+isoforest-v1",
    ))
    await session.flush()

    bot = FakeBot()
    sent = await send_morning_messages(session, date.today(), bot)

    assert sent == 1
    _, text = bot.sent[0]
    assert "PT Amanah" in text


@pytest.mark.asyncio
async def test_no_bot_returns_zero(db_session):
    session = db_session
    _seed_officer_company_scores(session)
    _add_case(session, "cA", priority=9000.0, due_date=date.today())

    sent = await send_morning_messages(session, date.today(), None)
    assert sent == 0


@pytest.mark.asyncio
async def test_cases_with_other_due_dates_are_ignored(db_session):
    session = db_session
    _seed_officer_company_scores(session)
    _add_case(session, "cA", priority=9000.0, due_date=date.today() + timedelta(days=2))

    bot = FakeBot()
    sent = await send_morning_messages(session, date.today(), bot)
    assert sent == 0


def test_risk_level_from_rules():
    rules = get_rules()
    assert risk_level(90.0, rules) == "high"
    assert risk_level(rules.risk_labels["high"], rules) == "high"
    assert risk_level(50.0, rules) == "medium"
    assert risk_level(10.0, rules) == "low"
