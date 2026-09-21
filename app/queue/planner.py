"""
Capacity-aware daily queue planner — PRD §11.5.

Algorithm: greedy knapsack per officer per day.
  priority_score = P_valid × est_mid / (visit_hours + travel_hours) × urgency
  Sorted descending, fill until daily_hours quota exhausted.
  20% of slots reserved for "exploration" (medium-score cases).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RulesConfig, get_rules, get_settings
from app.db.models import CaseFile, Company, Officer, Region, RiskScore, WorkerReport

logger = logging.getLogger(__name__)


@dataclass
class CaseCandidate:
    company_id: str
    company_name: str
    region_id: str
    risk: float
    p_valid: float
    est_mid: int
    est_low: int
    est_high: int
    signals_json: str
    worker_report_count: int
    priority_score: float = 0.0
    visit_hours: float = 3.0
    urgency: float = 1.0


async def build_daily_queue(
    session: AsyncSession,
    target_date: date | None = None,
    rules: RulesConfig | None = None,
) -> dict[str, list[str]]:
    """
    Build the daily case queue for all officers.

    Returns a dict: {officer_id: [case_id, ...]} for today's assignments.
    """
    target_date = target_date or date.today()
    rules = rules or get_rules()

    officers = await _load_officers(session)
    if not officers:
        logger.warning("planner: no officers found")
        return {}

    all_cases = await _load_escalatable_cases(session, rules)
    if not all_cases:
        logger.info("planner: no escalatable cases for %s", target_date)
        return {}

    _score_candidates(all_cases, rules)

    assignments: dict[str, list[str]] = {}

    for officer in officers:
        region_cases = [c for c in all_cases if c.region_id == officer.region_id]
        if not region_cases:
            continue

        selected = _knapsack_select(region_cases, officer.daily_hours, rules)
        case_ids: list[str] = []

        for candidate in selected:
            case_id = await _upsert_case(session, candidate, officer.officer_id, target_date, rules)
            case_ids.append(case_id)

        assignments[officer.officer_id] = case_ids
        logger.info(
            "planner: assigned %d cases to officer %s", len(case_ids), officer.officer_id
        )

    return assignments


# ---------------------------------------------------------------------------
# Escalation filter
# ---------------------------------------------------------------------------

async def _load_escalatable_cases(
    session: AsyncSession, rules: RulesConfig
) -> list[CaseCandidate]:
    """
    Load companies that meet escalation criteria (FR-C5):
      (a) ≥k independent worker reports AND data anomaly present, OR
      (b) risk score ≥ data_only_threshold
    """
    k = rules.min_independent_reports
    high_thresh = rules.data_only_risk_threshold

    # Count independent worker reports per company
    report_counts: dict[str, int] = {}
    report_result = await session.execute(
        select(
            WorkerReport.company_id,
        )
    )
    for (cid,) in report_result.all():
        report_counts[cid] = report_counts.get(cid, 0) + 1

    # Load latest risk scores
    result = await session.execute(
        select(
            RiskScore.company_id,
            RiskScore.risk,
            RiskScore.p_valid,
            RiskScore.est_mid,
            RiskScore.est_low,
            RiskScore.est_high,
            RiskScore.signals_json,
            Company.name,
            Company.region_id,
        )
        .join(Company, Company.company_id == RiskScore.company_id)
        .where(
            RiskScore.as_of == select(RiskScore.as_of)
            .order_by(RiskScore.as_of.desc())
            .limit(1)
            .scalar_subquery()
        )
    )

    candidates: list[CaseCandidate] = []
    for row in result.all():
        cid = row.company_id
        report_count = report_counts.get(cid, 0)
        risk = row.risk

        has_data_anomaly = risk > 30  # any notable data signal qualifies
        enough_reports = report_count >= k

        qualifies = (enough_reports and has_data_anomaly) or (risk >= high_thresh)
        if not qualifies:
            continue

        candidates.append(
            CaseCandidate(
                company_id=cid,
                company_name=row.name,
                region_id=row.region_id,
                risk=risk,
                p_valid=row.p_valid,
                est_mid=row.est_mid or 0,
                est_low=row.est_low or 0,
                est_high=row.est_high or 0,
                signals_json=row.signals_json,
                worker_report_count=report_count,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

def _score_candidates(cases: list[CaseCandidate], rules: RulesConfig) -> None:
    """Assign priority_score in-place."""
    default_hours = rules.planner["default_visit_hours"]
    for c in cases:
        c.visit_hours = default_hours
        # priority = P(valid) × est_mid / cost × urgency
        cost = c.visit_hours + 1.0  # +1h travel placeholder
        c.priority_score = (c.p_valid * c.est_mid / max(cost, 0.1)) * c.urgency


# ---------------------------------------------------------------------------
# Greedy knapsack with exploration slots
# ---------------------------------------------------------------------------

def _knapsack_select(
    candidates: list[CaseCandidate],
    daily_hours: float,
    rules: RulesConfig,
) -> list[CaseCandidate]:
    """
    Select cases for an officer using greedy knapsack.
    20% of capacity reserved for exploration (medium-score cases).
    """
    explore_share = rules.planner["explore_share"]
    explore_hours = daily_hours * explore_share
    exploit_hours = daily_hours - explore_hours

    sorted_by_priority = sorted(candidates, key=lambda c: c.priority_score, reverse=True)

    selected: list[CaseCandidate] = []
    hours_used = 0.0

    # Exploitation: top-scoring cases
    for case in sorted_by_priority:
        if hours_used + case.visit_hours > exploit_hours:
            break
        selected.append(case)
        hours_used += case.visit_hours

    # Exploration: medium-score cases (not already selected)
    remaining = [c for c in sorted_by_priority if c not in selected]
    mid_point = len(remaining) // 2
    explore_pool = remaining[mid_point // 2: mid_point + mid_point // 2]

    import random
    random.shuffle(explore_pool)

    explore_used = 0.0
    for case in explore_pool:
        if explore_used + case.visit_hours > explore_hours:
            break
        selected.append(case)
        explore_used += case.visit_hours

    return selected


# ---------------------------------------------------------------------------
# Case upsert
# ---------------------------------------------------------------------------

async def _upsert_case(
    session: AsyncSession,
    candidate: CaseCandidate,
    officer_id: str,
    target_date: date,
    rules: RulesConfig,
) -> str:
    """Create or reuse a CaseFile, generate access token, return case_id."""
    from uuid import uuid4

    settings = get_settings()

    # Check if an open case already exists for this company
    existing = await session.execute(
        select(CaseFile)
        .where(CaseFile.company_id == candidate.company_id)
        .where(CaseFile.status.in_(["pending", "assigned", "snoozed"]))
        .limit(1)
    )
    case = existing.scalar_one_or_none()

    token = secrets.token_hex(16)  # 128-bit random token
    token_hash = sha256(token.encode()).hexdigest()
    token_expires = datetime.utcnow() + timedelta(hours=settings.case_token_ttl_hours)

    signals = json.loads(candidate.signals_json) if candidate.signals_json else {}
    reasons = _build_reasons(candidate, signals)
    checklist = _build_checklist(signals)

    if case is None:
        case = CaseFile(
            case_id=str(uuid4()),
            company_id=candidate.company_id,
            status="assigned",
            priority=candidate.priority_score,
            assigned_officer=officer_id,
            due_date=target_date + timedelta(days=1),
            reasons_json=json.dumps(reasons),
            checklist_json=json.dumps(checklist),
            access_token_hash=token_hash,
            token_expires=token_expires,
        )
        session.add(case)
    else:
        case.status = "assigned"
        case.assigned_officer = officer_id
        case.priority = candidate.priority_score
        case.access_token_hash = token_hash
        case.token_expires = token_expires
        case.reasons_json = json.dumps(reasons)
        case.checklist_json = json.dumps(checklist)

    await session.flush()
    return case.case_id


def _build_reasons(candidate: CaseCandidate, signals: dict) -> list[dict]:
    reasons = []

    if candidate.worker_report_count >= 1:
        reasons.append({
            "title": "Laporan Pekerja",
            "evidence": f"{candidate.worker_report_count} laporan pekerja independen",
            "source": "Laporan pekerja anonim",
        })

    signal_labels = {
        "C1_under_reporting": ("Indikasi Upah Dilaporkan Lebih Rendah", "Data upah terdaftar vs UMK/sektor"),
        "C2_headcount_gap": ("Selisih Jumlah Pekerja", "Estimasi headcount vs yang terdaftar di BPJS"),
        "C3_remit_inconsistent": ("Setoran Tidak Konsisten", "Riwayat pembayaran iuran 3 bulan terakhir"),
        "C4_uniform_wage": ("Upah Seragam Mencurigakan", "Semua pekerja terdaftar persis di UMK"),
        "C5_churn": ("Churn Pekerja Tidak Wajar", "Mutasi keluar-masuk menjelang periode audit"),
    }

    top_signals = sorted(signals.items(), key=lambda x: x[1], reverse=True)[:2]
    for sig_key, contrib in top_signals:
        if sig_key in signal_labels and contrib > 0.1:
            title, source = signal_labels[sig_key]
            reasons.append({
                "title": title,
                "evidence": f"Kontribusi skor: {contrib:.0%}",
                "source": source,
            })

    return reasons[:3]


def _build_checklist(signals: dict) -> list[dict]:
    items = [
        {"item": "Minta daftar pekerja terdaftar BPJS bulan ini", "checked": False},
        {"item": "Minta bukti setoran iuran 3 bulan terakhir", "checked": False},
        {"item": "Verifikasi jumlah pekerja aktif vs yang terdaftar", "checked": False},
    ]

    if signals.get("C1_under_reporting", 0) > 0.2:
        items.append({"item": "Minta daftar gaji/payroll bulan berjalan", "checked": False})

    if signals.get("C3_remit_inconsistent", 0) > 0.2:
        items.append({"item": "Konfirmasi rekening dan bukti transfer ke BPJS", "checked": False})

    return items


# ---------------------------------------------------------------------------
# Officers loader
# ---------------------------------------------------------------------------

async def _load_officers(session: AsyncSession) -> list[Officer]:
    result = await session.execute(select(Officer))
    return list(result.scalars().all())
