"""
Case page routes — officer-facing case view and action endpoints.

Authentication: 128-bit token in URL, stored as SHA-256 hash in DB.
Write actions (action, outcome) invalidate the token (single-use).
Includes Officer Queue Portal (/portal) and Slip Verification Web Demo (/verify).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.db.models import (
    CaseFile,
    Company,
    Enrollment,
    Officer,
    Region,
    RiskScore,
    SectorBenchmark,
    VisitOutcome,
    WorkerReport,
)
from app.db.session import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)


def _format_idr(val: int | float | None) -> str:
    if val is None:
        return "Rp 0"
    return f"Rp {int(val):,}".replace(",", ".")


async def _get_case_by_token(token: str, session: AsyncSession) -> CaseFile:
    """Validate token and return CaseFile, raising 403/410 on invalid/expired."""
    token_hash = sha256(token.encode()).hexdigest()
    result = await session.execute(
        select(CaseFile).where(CaseFile.access_token_hash == token_hash)
    )
    case = result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=403, detail="Token tidak valid atau telah digunakan.")

    if case.token_expires and case.token_expires < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Token telah kedaluwarsa (berlaku 24 jam).")

    return case


@router.get("/case/{token}", response_class=HTMLResponse)
async def case_page(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
):
    case = await _get_case_by_token(token, session)

    company_result = await session.execute(
        select(Company, Region)
        .join(Region, Region.region_id == Company.region_id)
        .where(Company.company_id == case.company_id)
    )
    company_row = company_result.first()
    if not company_row:
        raise HTTPException(status_code=404, detail="Data badan usaha tidak ditemukan.")

    company, region = company_row

    # Latest risk score
    score_result = await session.execute(
        select(RiskScore)
        .where(RiskScore.company_id == case.company_id)
        .order_by(RiskScore.as_of.desc())
        .limit(1)
    )
    score = score_result.scalar_one_or_none()

    reasons = json.loads(case.reasons_json) if case.reasons_json else []
    checklist = json.loads(case.checklist_json) if case.checklist_json else []

    # Risk level styling
    from app.config import get_rules
    rules = get_rules()
    labels = rules.risk_labels
    risk_val = score.risk if score else 0
    if risk_val >= labels["high"]:
        risk_label, risk_color = "Tinggi", "red"
    elif risk_val >= labels["medium"]:
        risk_label, risk_color = "Sedang", "orange"
    else:
        risk_label, risk_color = "Rendah", "yellow"

    # Estimation strings
    est_str = "–"
    est_mid_formatted = "Rp 0"
    est_12m_formatted = "Rp 0"
    if score and score.est_low is not None and score.est_high is not None:
        def _fmt(n):
            return f"Rp{n // 1_000_000:.1f} jt" if n >= 1_000_000 else f"Rp{n // 1_000} rb"
        est_str = f"{_fmt(score.est_low)} – {_fmt(score.est_high)}"
        est_mid_formatted = _format_idr(score.est_mid)
        est_12m_formatted = _format_idr(score.est_mid * 12)

    # 1-sentence layman summary (PRD §14.1 butir 2)
    signals = json.loads(score.signals_json) if score and score.signals_json else {}
    if signals.get("C1_under_reporting", 0) >= 0.4:
        summary_sentence = "Indikasi upah dilaporkan 30–50% lebih rendah dari slip gaji riil pekerja; selisih potongan 1% dan setoran 4% terdeteksi konsisten."
    elif signals.get("C2_headcount_gap", 0) >= 0.4:
        summary_sentence = "Indikasi pendaftaran pekerja sebagian; estimasi pekerja fisik aktif di lapangan melampaui jumlah tenaga kerja terdaftar pada sistem BPJS."
    elif signals.get("C3_remit_inconsistent", 0) >= 0.3:
        summary_sentence = "Indikasi penggelapan iuran; gaji pekerja terpotong pada slip namun bukti setoran kas badan usaha tercatat menunggak."
    elif signals.get("C6_worker_reports", 0) >= 0.5:
        summary_sentence = "Terverifikasi sejumlah laporan mandiri independen dari pekerja aktif terkait pemotongan iuran yang tidak sesuai regulasi."
    else:
        summary_sentence = "Indikasi ketidakselarasan iuran berdasarkan verifikasi silang triangulasi data kepesertaan dan setoran."

    # Sector benchmark & triangulation data (PRD §14.1 butir 5)
    bench_result = await session.execute(
        select(SectorBenchmark)
        .where(
            SectorBenchmark.sector == company.sector,
            SectorBenchmark.region_id == company.region_id,
        )
    )
    benchmark = bench_result.scalar_one_or_none()
    benchmark_wage = benchmark.median_wage if benchmark else region.umk

    # Sample reported wage from enrollment or company estimates
    avg_enroll_res = await session.execute(
        select(func.avg(Enrollment.reported_wage_base))
        .where(Enrollment.company_id == company.company_id)
    )
    avg_wage_val = avg_enroll_res.scalar()
    reported_wage = int(avg_wage_val) if avg_wage_val else int(region.umk * 0.95)

    # Inferred payslip wage (triangulated)
    if signals.get("C1_under_reporting", 0) >= 0.2:
        implied_wage = int(reported_wage * 1.45)
    else:
        implied_wage = int(reported_wage * 1.15)

    worker_rep = round(reported_wage * 0.01)
    worker_imp = round(implied_wage * 0.01)
    employer_rep = round(reported_wage * 0.04)
    employer_imp = round(implied_wage * 0.04)
    worker_gap = (worker_imp + employer_imp) - (worker_rep + employer_rep)
    compliance_pct = min(100, max(20, round((reported_wage / implied_wage) * 100)))

    tri = {
        "reported_wage_fmt": _format_idr(reported_wage),
        "implied_wage_fmt": _format_idr(implied_wage),
        "benchmark_wage_fmt": _format_idr(benchmark_wage),
        "worker_rep_fmt": _format_idr(worker_rep),
        "worker_imp_fmt": _format_idr(worker_imp),
        "employer_rep_fmt": _format_idr(employer_rep),
        "employer_imp_fmt": _format_idr(employer_imp),
        "worker_gap_fmt": _format_idr(worker_gap),
        "compliance_pct": compliance_pct,
    }

    # Past visits history (PRD §14.1 butir 7)
    hist_result = await session.execute(
        select(VisitOutcome)
        .join(CaseFile, CaseFile.case_id == VisitOutcome.case_id)
        .where(CaseFile.company_id == company.company_id)
        .order_by(VisitOutcome.created_at.desc())
        .limit(3)
    )
    history_rows = hist_result.scalars().all()
    history = [
        {
            "result": h.result,
            "date": h.created_at.strftime("%d %b %Y") if h.created_at else "-",
            "note": h.note,
        }
        for h in history_rows
    ]

    return templates.TemplateResponse(
        request=request,
        name="case.html",
        context={
            "case": case,
            "company": company,
            "region": region,
            "score": score,
            "risk_label": risk_label,
            "risk_color": risk_color,
            "est_str": est_str,
            "est_mid_formatted": est_mid_formatted,
            "est_12m_formatted": est_12m_formatted,
            "summary_sentence": summary_sentence,
            "reasons": reasons,
            "checklist": checklist,
            "tri": tri,
            "history": history,
            "token": token,
        },
    )


@router.post("/case/{token}/action")
async def case_action(
    token: str,
    action: str = Form(...),
    reason: str = Form(default=""),
    session: AsyncSession = Depends(get_db),
):
    """Ambil / Tunda / Bukan Prioritas — write action invalidates token after use."""
    valid_actions = {"ambil", "tunda", "bukan_prioritas"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Aksi tidak valid: {action}")

    case = await _get_case_by_token(token, session)

    status_map = {"ambil": "assigned", "tunda": "snoozed", "bukan_prioritas": "closed"}
    case.status = status_map[action]

    # Invalidate token for write actions
    case.access_token_hash = None
    case.token_expires = None

    await write_audit(
        session,
        actor=case.assigned_officer or "officer",
        action="CASE_ACTION",
        object_ref=case.case_id,
        meta={"action": action, "reason": reason},
    )
    await session.commit()

    return {"status": "ok", "case_id": case.case_id, "new_status": case.status}


@router.post("/case/{token}/outcome")
async def submit_outcome(
    token: str,
    result: str = Form(...),
    confirmed_modus: str = Form(default=""),
    found_amount: int = Form(default=0),
    note: str = Form(default=""),
    session: AsyncSession = Depends(get_db),
):
    """Submit visit outcome — closes the case."""
    valid_results = {"TERBUKTI", "TIDAK_TERBUKTI", "PERLU_TINDAK_LANJUT"}
    if result not in valid_results:
        raise HTTPException(status_code=400, detail="Hasil tidak valid.")

    case = await _get_case_by_token(token, session)

    outcome = VisitOutcome(
        outcome_id=str(uuid4()),
        case_id=case.case_id,
        result=result,
        confirmed_modus=confirmed_modus or None,
        found_amount=found_amount or None,
        note=note or None,
    )
    session.add(outcome)

    case.status = "visited"
    case.access_token_hash = None
    case.token_expires = None

    await write_audit(
        session,
        actor=case.assigned_officer or "officer",
        action="VISIT_OUTCOME",
        object_ref=case.case_id,
        meta={"result": result, "found_amount": found_amount, "modus": confirmed_modus},
    )
    await session.commit()

    return {"status": "ok", "outcome_id": outcome.outcome_id}


# ---------------------------------------------------------------------------
# Officer Queue Portal (/ & /portal)
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
@router.get("/portal", response_class=HTMLResponse)
async def officer_portal(
    request: Request,
    officer_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Officer Queue Portal — shows today's assigned cases, workload, and direct case links."""
    # Fetch officers list
    officers_res = await session.execute(select(Officer).order_by(Officer.name))
    officers = officers_res.scalars().all()

    if not officers:
        # Fallback dummy officer if DB empty
        current_officer = Officer(
            officer_id="officer-01",
            name="Ahmad Fauzi",
            region_id="jkt-utara",
            daily_hours=8.0,
        )
        officers = [current_officer]
    else:
        if officer_id:
            current_officer = next((o for o in officers if o.officer_id == officer_id), officers[0])
        else:
            current_officer = officers[0]

    # Query assigned cases for this officer
    cases_res = await session.execute(
        select(CaseFile, Company, Region, RiskScore)
        .join(Company, Company.company_id == CaseFile.company_id)
        .join(Region, Region.region_id == Company.region_id)
        .outerjoin(RiskScore, RiskScore.company_id == Company.company_id)
        .where(CaseFile.assigned_officer == current_officer.officer_id)
        .order_by(CaseFile.priority.desc())
    )
    rows = cases_res.all()

    today_str = datetime.now().strftime("%A, %d %B %Y")
    cases_data = []
    total_est_mid = 0
    high_priority_count = 0

    for case, comp, reg, score in rows:
        # Ensure an active token exists for each case so the officer can click it
        token_plain = secrets.token_urlsafe(16)
        token_hash = sha256(token_plain.encode()).hexdigest()
        case.access_token_hash = token_hash
        case.token_expires = datetime.utcnow() + timedelta(hours=24)

        est_mid = score.est_mid if score and score.est_mid else 12_500_000
        total_est_mid += est_mid

        risk_val = score.risk if score else (case.priority / 100 if case.priority else 65)
        if risk_val >= 70:
            risk_label = "Tinggi"
            high_priority_count += 1
        elif risk_val >= 40:
            risk_label = "Sedang"
        else:
            risk_label = "Rendah"

        reasons = json.loads(case.reasons_json) if case.reasons_json else []
        top_reason_text = reasons[0]["title"] if reasons else "Indikasi diskrepansi upah"

        cases_data.append({
            "case_id": case.case_id,
            "company_name": comp.name,
            "npp": comp.npp,
            "sector": comp.sector,
            "city": reg.city,
            "registered_headcount": comp.registered_headcount,
            "risk_label": risk_label,
            "est_mid_fmt": _format_idr(est_mid),
            "top_signal_text": top_reason_text,
            "due_date": case.due_date.strftime("%d %b %Y") if case.due_date else "Hari Ini",
            "status": case.status,
            "token": token_plain,
        })

    await session.commit()

    hours_used = min(current_officer.daily_hours, round(len(cases_data) * 1.8, 1))

    return templates.TemplateResponse(
        request=request,
        name="portal.html",
        context={
            "officers": officers,
            "current_officer": current_officer,
            "today_str": today_str,
            "cases": cases_data,
            "total_est_mid_fmt": _format_idr(total_est_mid),
            "high_priority_count": high_priority_count,
            "hours_used": hours_used,
        },
    )


# ---------------------------------------------------------------------------
# Worker Payslip Verification Web Demo (/verify)
# ---------------------------------------------------------------------------

@router.get("/verify", response_class=HTMLResponse)
async def slip_verify_page(request: Request):
    """Worker Payslip Verification Web Demo page."""
    return templates.TemplateResponse(request=request, name="verify.html", context={})
