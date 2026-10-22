"""
Planner antrean harian kapasitas-sadari — PRD §11.5.

Greedy knapsack per petugas per hari:
  priority_score = P_valid × est_mid / (visit_hours + travel_hours) × urgency

Kasus diurut menurun hingga kuota jam harian hampir penuh; sisanya disisihkan
untuk eksplorasi kasus papan-tengah secara acak (deterministik bila RNG disuntik).
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cases.service import SIGNAL_LABELS, upsert_active_case
from app.config import RulesConfig, get_rules
from app.db.models import CaseFile, Company, Officer, RiskScore, WorkerReport

logger = logging.getLogger(__name__)

_OPEN_STATUSES = ("pending", "assigned", "snoozed")


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
    visit_hours: float = 0.0
    urgency: float = 1.0


async def build_daily_queue(
    session: AsyncSession,
    target_date: date | None = None,
    rules: RulesConfig | None = None,
    rng: random.Random | None = None,
) -> dict[str, list[str]]:
    """Susun antrean kasus harian seluruh petugas; kembalikan {officer_id: [case_id, ...]}."""
    target_date = target_date or date.today()
    rules = rules or get_rules()
    rng = rng if rng is not None else random.Random()

    officers = await _load_officers(session)
    if not officers:
        logger.warning("planner: tidak ada petugas terdaftar")
        return {}

    candidates = await _load_escalatable_cases(session, rules)
    if not candidates:
        logger.info("planner: tidak ada kasus layak eskalasi untuk %s", target_date)
        return {}

    waiting_days = await _open_case_waiting_days(session)
    _score_candidates(candidates, rules, waiting_days)

    assignments: dict[str, list[str]] = {}
    for officer in officers:
        region_cases = [c for c in candidates if c.region_id == officer.region_id]
        if not region_cases:
            continue

        selected = _knapsack_select(region_cases, float(officer.daily_hours), rules, rng)
        case_ids: list[str] = []
        for candidate in selected:
            case = await _upsert_case(session, candidate, officer.officer_id, target_date, rules)
            case_ids.append(case.case_id)

        assignments[officer.officer_id] = case_ids
        logger.info("planner: %d kasus diassign ke petugas %s", len(case_ids), officer.officer_id)

    return assignments


async def _load_officers(session: AsyncSession) -> list[Officer]:
    """Seluruh petugas aktif; penugasan harian dilakukan per wilayah."""
    result = await session.execute(select(Officer))
    return list(result.scalars().all())


async def _load_escalatable_cases(
    session: AsyncSession, rules: RulesConfig
) -> list[CaseCandidate]:
    """
    Muat perusahaan yang memenuhi kriteria eskalasi (FR-C5):
      (a) ≥ k laporan pekerja independen DAN anomali data (bila diwajibkan), atau
      (b) risiko data-only ≥ ambang.
    """
    k = rules.min_independent_reports
    high_threshold = rules.data_only_risk_threshold
    anomaly_threshold = float(rules.scoring["anomaly_data_risk"])
    require_anomaly = bool(rules.escalation["require_data_anomaly"])

    counts = await session.execute(
        select(
            WorkerReport.company_id,
            func.count(func.distinct(WorkerReport.check_id)).label("independent"),
        ).group_by(WorkerReport.company_id)
    )
    report_counts: dict[str, int] = {row.company_id: int(row.independent) for row in counts}

    latest = (
        select(RiskScore.company_id, func.max(RiskScore.as_of).label("max_as_of"))
        .group_by(RiskScore.company_id)
        .subquery()
    )
    result = await session.execute(
        select(RiskScore, Company)
        .join(
            latest,
            (latest.c.company_id == RiskScore.company_id)
            & (latest.c.max_as_of == RiskScore.as_of),
        )
        .join(Company, Company.company_id == RiskScore.company_id)
    )

    candidates: list[CaseCandidate] = []
    for score, company in result.all():
        report_count = report_counts.get(score.company_id, 0)
        risk = float(score.risk)
        has_anomaly = risk > anomaly_threshold
        enough_reports = report_count >= k

        if require_anomaly:
            qualifies = (enough_reports and has_anomaly) or (risk >= high_threshold)
        else:
            qualifies = enough_reports or (risk >= high_threshold)
        if not qualifies:
            continue

        candidates.append(
            CaseCandidate(
                company_id=score.company_id,
                company_name=company.name,
                region_id=company.region_id,
                risk=risk,
                p_valid=float(score.p_valid),
                est_mid=int(score.est_mid or 0),
                est_low=int(score.est_low or 0),
                est_high=int(score.est_high or 0),
                signals_json=score.signals_json or "{}",
                worker_report_count=report_count,
            )
        )

    return candidates


async def _open_case_waiting_days(session: AsyncSession) -> dict[str, int]:
    """Hari kasus terbuka tertua telah menunggu, per perusahaan."""
    result = await session.execute(
        select(CaseFile.company_id, func.min(CaseFile.created_at))
        .where(CaseFile.status.in_(_OPEN_STATUSES))
        .group_by(CaseFile.company_id)
    )
    today = date.today()
    waiting: dict[str, int] = {}
    for company_id, created_at in result.all():
        if created_at is None:
            continue
        waiting[company_id] = max(0, (today - created_at.date()).days)
    return waiting


def _score_candidates(
    candidates: list[CaseCandidate],
    rules: RulesConfig,
    waiting_days: dict[str, int],
) -> None:
    """Isi visit_hours, urgency, priority_score langsung pada tiap kandidat."""
    default_hours = float(rules.planner["default_visit_hours"])
    travel_hours = float(rules.planner["travel_hours"])
    min_cost = float(rules.planner["min_cost_hours"])
    increment = float(rules.planner["urgency_increment_per_day"])
    urgency_cap = float(rules.planner["urgency_max"])

    for candidate in candidates:
        candidate.visit_hours = default_hours
        days = waiting_days.get(candidate.company_id, 0)
        candidate.urgency = min(urgency_cap, 1.0 + increment * days)
        cost = candidate.visit_hours + travel_hours
        candidate.priority_score = (
            candidate.p_valid * candidate.est_mid / max(cost, min_cost) * candidate.urgency
        )


def _knapsack_select(
    candidates: list[CaseCandidate],
    daily_hours: float,
    rules: RulesConfig,
    rng: random.Random,
) -> list[CaseCandidate]:
    """Pilih kasus greedy menurut prioritas; sisihkan slot eksplorasi dari papan-tengah."""
    explore_share = float(rules.planner["explore_share"])
    band_lo, band_hi = (float(x) for x in rules.planner["explore_band"])
    exploit_hours = daily_hours * (1.0 - explore_share)
    explore_hours = daily_hours * explore_share

    ranked = sorted(candidates, key=lambda c: c.priority_score, reverse=True)

    selected: list[CaseCandidate] = []
    used = 0.0
    for candidate in ranked:
        if used + candidate.visit_hours > exploit_hours:
            break
        selected.append(candidate)
        used += candidate.visit_hours

    remaining = [c for c in ranked if c not in selected]
    lo = int(len(remaining) * band_lo)
    hi = max(lo, int(len(remaining) * band_hi))
    explore_pool = remaining[lo:hi] if len(remaining) >= 2 else remaining
    shuffled = list(explore_pool)
    rng.shuffle(shuffled)

    explore_used = 0.0
    for candidate in shuffled:
        if explore_used + candidate.visit_hours > explore_hours:
            break
        if candidate in selected:
            continue
        selected.append(candidate)
        explore_used += candidate.visit_hours

    return selected


async def _upsert_case(
    session: AsyncSession,
    candidate: CaseCandidate,
    officer_id: str,
    target_date: date,
    rules: RulesConfig,
) -> CaseFile:
    """Buat/pakai ulang kasus aktif dengan alasan, checklist, token, dan tenggat planner."""
    signals = json.loads(candidate.signals_json) if candidate.signals_json else {}
    case = await upsert_active_case(
        session,
        company_id=candidate.company_id,
        reasons=_build_reasons(candidate, signals, rules),
        checklist=_build_checklist(signals, rules),
        priority=candidate.priority_score,
        officer_id=officer_id,
        due_days=int(rules.planner["case_due_days"]),
    )
    await session.flush()
    return case


def _build_reasons(candidate: CaseCandidate, signals: dict, rules: RulesConfig) -> list[dict]:
    reason_floor = float(rules.thresholds["reason_contribution"])

    reasons: list[dict] = []
    if candidate.worker_report_count >= 1:
        reasons.append({
            "title": "Laporan Pekerja",
            "evidence": f"{candidate.worker_report_count} laporan pekerja independen",
            "source": "Laporan pekerja anonim",
        })

    top_signals = sorted(signals.items(), key=lambda kv: kv[1], reverse=True)
    for sig_key, contribution in top_signals:
        label = SIGNAL_LABELS.get(sig_key)
        if label and contribution > reason_floor:
            title, source = label
            reasons.append({
                "title": title,
                "evidence": f"Kontribusi skor: {float(contribution):.0%}",
                "source": source,
            })

    return reasons[:3]


def _build_checklist(signals: dict, rules: RulesConfig) -> list[dict]:
    signal_floor = float(rules.thresholds["checklist_signal"])

    items = [
        {"item": "Minta daftar pekerja terdaftar BPJS bulan ini", "checked": False},
        {"item": "Minta bukti setoran iuran belakangan", "checked": False},
        {"item": "Verifikasi jumlah pekerja aktif vs yang terdaftar", "checked": False},
    ]
    if float(signals.get("C1_under_reporting", 0) or 0) > signal_floor:
        items.append({"item": "Minta daftar gaji/payroll bulan berjalan", "checked": False})
    if float(signals.get("C3_remit_inconsistent", 0) or 0) > signal_floor:
        items.append({"item": "Konfirmasi rekening dan bukti transfer ke BPJS", "checked": False})

    return items
