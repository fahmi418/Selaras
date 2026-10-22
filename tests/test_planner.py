"""Tests planner: urgency aging, cost jam, knapsack deterministik, kriteria eskalasi."""

from __future__ import annotations

import random
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.config import get_rules
from app.db.models import CaseFile, Company, Officer, Region, RiskScore, WorkerReport
from app.queue.planner import (
    CaseCandidate,
    _build_checklist,
    _build_reasons,
    _knapsack_select,
    _score_candidates,
    build_daily_queue,
)


def _candidate(company_id: str, p_valid: float, est_mid: int) -> CaseCandidate:
    return CaseCandidate(
        company_id=company_id,
        company_name=f"PT {company_id}",
        region_id="r1",
        risk=50.0,
        p_valid=p_valid,
        est_mid=est_mid,
        est_low=est_mid // 2,
        est_high=int(est_mid * 1.3),
        signals_json="{}",
        worker_report_count=0,
    )


def _tuned_rules() -> "object":
    rules = deepcopy(get_rules())
    rules.planner["default_visit_hours"] = 3.0
    rules.planner["travel_hours"] = 1.0
    rules.planner["min_cost_hours"] = 0.1
    rules.planner["urgency_increment_per_day"] = 0.1
    rules.planner["urgency_max"] = 2.0
    return rules


def test_score_candidates_urgency_aging_and_priority():
    rules = _tuned_rules()
    candidates = [_candidate("a", 0.8, 400_000)]

    _score_candidates(candidates, rules, {"a": 5})
    assert candidates[0].urgency == pytest.approx(1.5)
    # priority = p * est_mid / (visit+travel) * urgency
    assert candidates[0].priority_score == pytest.approx(0.8 * 400_000 / 4.0 * 1.5)

    _score_candidates(candidates, rules, {"a": 0})
    assert candidates[0].urgency == pytest.approx(1.0)

    _score_candidates(candidates, rules, {"a": 20})
    assert candidates[0].urgency == pytest.approx(rules.planner["urgency_max"])


def test_knapsack_respects_exploit_capacity():
    rules = _tuned_rules()
    candidates = [
        _candidate("c1", 1.0, 400_000),
        _candidate("c2", 1.0, 360_000),
        _candidate("c3", 1.0, 320_000),
        _candidate("c4", 1.0, 280_000),
    ]
    _score_candidates(candidates, rules, {})

    selected = _knapsack_select(candidates, 8.0, rules, random.Random(42))
    ids = [c.company_id for c in selected]
    # daily 8 jam: exploit 6.4 jam muat 2 kunjungan (6 jam); eksplorasi 1.6 jam tak muat 3 jam.
    assert ids == ["c1", "c2"]


def test_knapsack_explore_picks_from_mid_band():
    rules = deepcopy(get_rules())
    rules.planner["default_visit_hours"] = 2.0
    rules.planner["travel_hours"] = 0.0

    candidates = [_candidate(f"c{i}", 1.0, 400_000 - i * 20_000) for i in range(10)]
    _score_candidates(candidates, rules, {})

    selected = _knapsack_select(candidates, 10.0, rules, random.Random(1))
    ids = {c.company_id for c in selected}

    assert "c0" in ids and "c1" in ids and "c2" in ids and "c3" in ids
    total_hours = sum(c.visit_hours for c in selected)
    assert total_hours <= 10.0
    # Sisanya harus dari papan-tengah: band 0.4-0.8 dari 6 kandidat tersisa = indeks 2-3 (c6, c7).
    extras = ids - {"c0", "c1", "c2", "c3"}
    assert extras <= {"c6", "c7"}
    assert len(selected) == 5


def test_knapsack_deterministic_with_same_seed():
    rules = deepcopy(get_rules())
    rules.planner["default_visit_hours"] = 2.0
    rules.planner["travel_hours"] = 0.0

    candidates = [_candidate(f"c{i}", 1.0, 400_000 - i * 20_000) for i in range(10)]
    _score_candidates(candidates, rules, {})

    first = [c.company_id for c in _knapsack_select(candidates, 10.0, rules, random.Random(9))]
    second = [c.company_id for c in _knapsack_select(candidates, 10.0, rules, random.Random(9))]
    assert first == second


def test_build_reasons_and_checklist_from_rules():
    rules = get_rules()
    candidate = _candidate("a", 0.5, 100_000)
    candidate.worker_report_count = 1
    signals = {
        "C1_under_reporting": 0.9,
        "C2_headcount_gap": 0.05,   # di bawah ambang reason
        "C3_remit_inconsistent": 0.6,
    }
    reasons = _build_reasons(candidate, signals, rules)
    titles = [r["title"] for r in reasons]
    assert titles[0] == "Laporan Pekerja"
    assert "Indikasi Upah Dilaporkan Lebih Rendah" in titles            # C1
    assert "Selisih Jumlah Pekerja" not in titles                       # C2 di bawah lantai
    assert len(reasons) <= 3

    checklist = _build_checklist(signals, rules)
    items = [i["item"] for i in checklist]
    assert "Minta daftar gaji/payroll bulan berjalan" in items           # C1 > floor
    assert "Konfirmasi rekening dan bukti transfer ke BPJS" in items    # C3 > floor
    assert all(not i["checked"] for i in checklist)


def _seed_region_company_officer(session) -> None:
    session.add(Region(region_id="r1", province="P", city="C", umk=4_000_000, umk_year=2026))
    session.add(Officer(
        officer_id="o1", name="Petugas Satu", region_id="r1", daily_hours=8,
    ))
    session.add(Company(
        company_id="c1", npp="1-1", name="PT Alpha", sector="jasa", region_id="r1",
        size_bucket="small", est_headcount=10, registered_headcount=10, status="active",
    ))
    session.add(Company(
        company_id="c2", npp="1-2", name="PT Beta", sector="jasa", region_id="r1",
        size_bucket="small", est_headcount=10, registered_headcount=10, status="active",
    ))


@pytest.mark.asyncio
async def test_report_only_without_anomaly_is_not_escalated(db_session):
    session = db_session
    _seed_region_company_officer(session)
    session.add(RiskScore(
        company_id="c1", as_of=date.today(), rule_score=0.2, anomaly_score=0.0,
        risk=20.0, p_valid=0.2, est_mid=100_000, est_low=50_000, est_high=130_000,
        signals_json="{}", model_version="rule+isoforest-v1",
    ))
    session.add(RiskScore(
        company_id="c2", as_of=date.today(), rule_score=0.85, anomaly_score=0.5,
        risk=85.0, p_valid=0.85, est_mid=2_000_000, est_low=1_000_000, est_high=2_600_000,
        signals_json="{}", model_version="rule+isoforest-v1",
    ))
    session.add(WorkerReport(
        report_id="wr-1", check_id="chk-1", company_id="c1",
        category="W1", note="upah ditekan",
    ))

    rules = get_rules()
    assignments = await build_daily_queue(session, date.today(), rules, random.Random(3))

    # c1: 1 laporan & tanpa anomali -> tidak lolos; c2: risiko >= ambang data-only -> lolos.
    assert list(assignments.keys()) == ["o1"]
    case_ids = assignments["o1"]
    assert len(case_ids) == 1
    assigned = await session.get(CaseFile, case_ids[0])
    assert assigned.company_id == "c2"


@pytest.mark.asyncio
async def test_relaxed_escalation_assigns_report_only_case(db_session):
    session = db_session
    _seed_region_company_officer(session)
    session.add(RiskScore(
        company_id="c1", as_of=date.today(), rule_score=0.2, anomaly_score=0.0,
        risk=20.0, p_valid=0.2, est_mid=100_000, est_low=50_000, est_high=130_000,
        signals_json="{}", model_version="rule+isoforest-v1",
    ))
    session.add(WorkerReport(
        report_id="wr-2", check_id="chk-2", company_id="c1", category="W1", note="x"
    ))

    rules = deepcopy(get_rules())
    rules.escalation["require_data_anomaly"] = False
    rules.escalation["min_independent_reports"] = 1

    assignments = await build_daily_queue(session, date.today(), rules, random.Random(3))

    assert list(assignments.keys()) == ["o1"]
    assigned = await session.get(CaseFile, assignments["o1"][0])
    assert assigned.company_id == "c1"
    assert assigned.status == "assigned"
    rules_default = get_rules()
    due_days = int(rules_default.planner["case_due_days"])
    assert assigned.due_date == date.today() + timedelta(days=due_days)
