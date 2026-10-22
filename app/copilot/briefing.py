"""
Pre-Audit Briefing Generator — Horizon 2 Pilar 1.

Merangkum profil risiko, riwayat setoran, laporan pekerja, dan menyusun draf
strategi wawancara audit (deterministik — tanpa LLM) sebelum kunjungan.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cases.service import SIGNAL_LABELS, find_open_case
from app.config import RulesConfig, get_rules
from app.db.models import Billing, CaseFile, Company, WorkerReport
from app.risk.scoring import get_latest_score


def top_prescriptive_actions(
    signals: dict[str, Any], rules: RulesConfig
) -> list[dict[str, str]]:
    """Saran tindakan preskriptif dari kontribusi sinyal terbesar (sesuai rules.yaml)."""
    catalog = rules.briefing["prescriptive_actions"]
    floor = float(rules.thresholds["reason_contribution"])

    ranked = sorted(
        ((key, float(value or 0)) for key, value in signals.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    actions = []
    for key, contribution in ranked:
        action = catalog.get(key)
        if action is None or contribution <= floor:
            continue
        label, source = SIGNAL_LABELS.get(key, (key, key))
        actions.append(
            {
                "signal": key,
                "signal_label": label,
                "contribution": contribution,
                "action": action,
                "source": source,
            }
        )
    max_actions = int(rules.briefing["interview_goals_max"])
    return actions[:max_actions]


async def build_briefing(
    session: AsyncSession,
    case: CaseFile,
    rules: RulesConfig | None = None,
) -> Optional[dict]:
    """Briefing pra-audit utuh untuk sebuah kasus; None bila perusahaan tidak aktif."""
    rules = rules or get_rules()
    company = await session.get(Company, case.company_id)
    if company is None:
        return None

    score = await get_latest_score(session, company.company_id)
    signals: dict[str, Any] = {}
    if score is not None:
        raw = json.loads(score.signals_json) if score.signals_json else {}
        signals = {k: float(v or 0) for k, v in raw.items()}

    billing_months = int(rules.features["billing_lookback_months"])
    billing_cutoff = _period_as_text(billing_months)
    billing_rows = (await session.execute(
        select(Billing.period, Billing.billed_amount, Billing.paid_amount)
        .where(Billing.company_id == company.company_id)
        .where(Billing.period >= billing_cutoff)
        .order_by(Billing.period)
    )).all()

    _, report_start = _month_window(rules.features["reports_lookback_months"])
    report_stats = (await session.execute(
        select(
            func.count(WorkerReport.report_id),
            func.count(func.distinct(WorkerReport.check_id)),
        )
        .where(WorkerReport.company_id == company.company_id)
        .where(WorkerReport.created_at >= report_start)
    )).one()

    checklist = json.loads(case.checklist_json) if case.checklist_json else []
    reasons = json.loads(case.reasons_json) if case.reasons_json else []

    briefing = {
        "case_id": case.case_id,
        "case_status": case.status,
        "case_priority": case.priority,
        "due_date": case.due_date.isoformat() if case.due_date else None,
        "reasons": reasons,
        "company": {
            "company_id": company.company_id,
            "name": company.name,
            "sector": company.sector,
            "region_id": company.region_id,
            "size_bucket": company.size_bucket,
            "est_headcount": company.est_headcount,
            "registered_headcount": company.registered_headcount,
        },
        "risk": None if score is None else {
            "risk": score.risk,
            "p_valid": score.p_valid,
            "est_low": score.est_low,
            "est_mid": score.est_mid,
            "est_high": score.est_high,
            "model_version": score.model_version,
            "signals": signals,
        },
        "billing_history": [
            {"period": period, "billed": billed, "paid": paid}
            for period, billed, paid in billing_rows
        ],
        "worker_reports": {
            "total": int(report_stats[0]),
            "independent_checks": int(report_stats[1]),
            "lookback_months": int(rules.features["reports_lookback_months"]),
        },
        "checklist_pending": [item for item in checklist if not item.get("checked")],
        "prescriptive_actions": top_prescriptive_actions(signals, rules),
    }
    return briefing


def _period_as_text(months: int) -> str:
    cutoff_year, cutoff_month = _month_window(months)
    return f"{cutoff_year:04d}-{cutoff_month:02d}"


def _month_window(months: int) -> tuple[int, int]:
    today = date.today()
    total = today.year * 12 + (today.month - 1) - (months - 1)
    return total // 12, total % 12 + 1


def format_briefing_text(briefing: dict) -> str:
    """Versi teks singkat untuk halaman kasus petugas (tanpa angka palsu)."""
    lines = [
        "📋 Briefing Pra-Audit",
        f"Perusahaan: {briefing['company']['name']} ({briefing['company']['size_bucket']})",
    ]
    risk = briefing["risk"]
    if risk is None:
        lines.append("Skor risiko: belum tersedia")
    else:
        lines.append(f"Skor risiko: {risk['risk']:.0f} · P(valid) {risk['p_valid']:.0%}")
        est_mid = risk["est_mid"] or 0
        lines.append(f"Potensi selisih iuran: Rp{est_mid:,.0f}")

    reports = briefing["worker_reports"]
    lines.append(
        f"Laporan pekerja: {reports['total']} ({reports['independent_checks']} check independen)"
    )

    if briefing["prescriptive_actions"]:
        lines.append("")
        lines.append("Strategi wawancara:")
        for i, action in enumerate(briefing["prescriptive_actions"], 1):
            lines.append(f"{i}. {action['action']}")

    if briefing["checklist_pending"]:
        lines.append("")
        lines.append("Checklist belum selesai:")
        for item in briefing["checklist_pending"]:
            lines.append(f"• {item['item']}")

    return "\n".join(lines)


async def briefing_for_case(session: AsyncSession, case_id: str) -> Optional[dict]:
    """Pintu masuk API: ambil kasus terbuka terakhir lalu susun briefing."""
    case = await session.get(CaseFile, case_id)
    if case is None:
        case = await find_open_case(session, case_id)
    if case is None:
        return None
    return await build_briefing(session, case)