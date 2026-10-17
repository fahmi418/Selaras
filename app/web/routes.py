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
from pydantic import BaseModel, Field
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
    SlipCheck,
    VisitOutcome,
    WorkerReport,
)
from app.db.session import get_db
from app.web.auth import auth_enabled, require_login
from app.web.triangulation import build_triangulation

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)


def _format_idr(val: int | float | None) -> str:
    if val is None:
        return "Rp 0"
    return f"Rp {int(val):,}".replace(",", ".")


def _normalize_checklist(raw: list) -> list[dict]:
    """Accept both legacy plain-string items and {"item", "required"} dicts."""
    items = []
    for entry in raw:
        if isinstance(entry, str):
            items.append({"item": entry, "required": False})
        elif isinstance(entry, dict) and entry.get("item"):
            items.append({"item": entry["item"], "required": bool(entry.get("required"))})
    return items


async def _get_case_by_token(token: str, session: AsyncSession) -> CaseFile:
    """Validate token and return CaseFile, raising 403/410 on invalid/expired."""
    token_clean = token.strip()
    token_hash = sha256(token_clean.encode()).hexdigest()
    result = await session.execute(
        select(CaseFile).where(CaseFile.access_token_hash == token_hash)
    )
    case = result.scalar_one_or_none()

    if not case:
        from app.config import get_settings
        if get_settings().demo_mode:
            # 1. Match by case_id
            case_by_id = (await session.execute(select(CaseFile).where(CaseFile.case_id == token_clean))).scalar_one_or_none()
            if case_by_id:
                return case_by_id
            # 2. Match first available case in demo mode
            any_case = (await session.execute(select(CaseFile).order_by(CaseFile.priority.desc()).limit(1))).scalar_one_or_none()
            if any_case:
                return any_case
        raise HTTPException(status_code=403, detail="Token tidak valid atau telah digunakan. Silakan buka kembali dari Portal Petugas (/portal).")

    if case.token_expires and case.token_expires < datetime.utcnow():
        from app.config import get_settings
        if not get_settings().demo_mode:
            raise HTTPException(status_code=410, detail="Token telah kedaluwarsa (berlaku 24 jam).")

    return case


@router.get("/case/{token}", response_class=HTMLResponse)
async def case_page(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
):
    case = await _get_case_by_token(token, session)

    # In demo mode, ensure active token is always available for write actions
    import secrets
    from datetime import datetime, timedelta
    active_token = token
    if not case.access_token_hash or (case.token_expires and case.token_expires < datetime.utcnow()):
        active_token = secrets.token_urlsafe(16)
        case.access_token_hash = sha256(active_token.encode()).hexdigest()
        case.token_expires = datetime.utcnow() + timedelta(hours=24)
        await session.commit()

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
    checklist = _normalize_checklist(json.loads(case.checklist_json) if case.checklist_json else [])

    # Risk level styling
    from app.config import get_rules
    rules = get_rules()
    labels = rules.risk_labels
    risk_val = score.risk if score else 0
    if score is None:
        risk_label, risk_color = "Belum dinilai", "gray"
    elif risk_val >= labels["high"]:
        risk_label, risk_color = "Tinggi", "red"
    elif risk_val >= labels["medium"]:
        risk_label, risk_color = "Sedang", "orange"
    else:
        risk_label, risk_color = "Rendah", "yellow"

    # Estimation strings
    est_str = "–"
    est_mid_formatted = "–"
    est_12m_formatted = "–"
    if score and score.est_low is not None and score.est_high is not None and score.est_mid is not None:
        def _fmt(n):
            return f"Rp{n // 1_000_000:.1f} jt" if n >= 1_000_000 else f"Rp{n // 1_000} rb"
        est_str = f"{_fmt(score.est_low)} – {_fmt(score.est_high)}"
        est_mid_formatted = _format_idr(score.est_mid)
        est_12m_formatted = _format_idr(score.est_mid * 12)

    # 1-sentence layman summary (PRD §14.1 butir 2)
    signals = json.loads(score.signals_json) if score and score.signals_json else {}
    if signals.get("C1_under_reporting", 0) >= 0.4:
        summary_sentence = "Indikasi upah yang dilaporkan ke BPJS lebih rendah dari upah riil pada slip gaji pekerja, sehingga potongan 1% dan setoran 4% ikut lebih kecil."
    elif signals.get("C2_headcount_gap", 0) >= 0.4:
        summary_sentence = "Indikasi pendaftaran pekerja sebagian; estimasi pekerja fisik aktif di lapangan melampaui jumlah tenaga kerja terdaftar pada sistem BPJS."
    elif signals.get("C3_remit_inconsistent", 0) >= 0.3:
        summary_sentence = "Indikasi penggelapan iuran; gaji pekerja terpotong pada slip namun bukti setoran kas badan usaha tercatat menunggak."
    elif signals.get("C6_worker_reports", 0) >= 0.5:
        summary_sentence = "Terverifikasi sejumlah laporan mandiri independen dari pekerja aktif terkait pemotongan iuran yang tidak sesuai regulasi."
    else:
        summary_sentence = "Indikasi ketidakselarasan iuran berdasarkan verifikasi silang triangulasi data kepesertaan dan setoran."

    # Latest extracted slip check from worker reports
    latest_slip_res = await session.execute(
        select(SlipCheck)
        .where(SlipCheck.company_id == company.company_id)
        .order_by(SlipCheck.created_at.desc())
        .limit(1)
    )
    latest_slip = latest_slip_res.scalar_one_or_none()

    # Sector benchmark & triangulation data (PRD §14.1 butir 5)
    bench_result = await session.execute(
        select(SectorBenchmark)
        .where(
            SectorBenchmark.sector == company.sector,
            SectorBenchmark.region_id == company.region_id,
        )
    )
    benchmark = bench_result.scalar_one_or_none()
    benchmark_wage = benchmark.median_wage if benchmark else region.umk  # UMK is the stated fallback benchmark

    # Reported wage on record at BPJS (None when the company has no enrollment rows)
    avg_enroll_res = await session.execute(
        select(func.avg(Enrollment.reported_wage_base))
        .where(Enrollment.company_id == company.company_id)
    )
    avg_wage_val = avg_enroll_res.scalar()

    tri = build_triangulation(
        reported_wage=avg_wage_val,
        slip_expected_deduction=latest_slip.expected_deduction if latest_slip else None,
        slip_actual_deduction=latest_slip.actual_deduction if latest_slip else None,
        benchmark_wage=benchmark_wage,
        rules=rules,
    )
    tri["slip_period"] = latest_slip.period if latest_slip else None

    # Format reasons so source and evidence are always clearly displayed
    formatted_reasons = []
    for r in reasons:
        r_title = r.get("title", "Indikasi Diskrepansi Upah")
        r_source = r.get("source") or ("Laporan Mandiri Slip Pekerja" if "Laporan" in r_title else "Data Kepesertaan BPJS")
        r_evidence = (
            r.get("evidence")
            or r.get("detail")
            or "Sinyal terdeteksi dari data kepesertaan dan setoran; lihat tabel triangulasi di bawah."
        )
        formatted_reasons.append({
            "title": r_title,
            "source": r_source,
            "evidence": r_evidence,
            "weight": r.get("weight", 0.5),
        })

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
            "reasons": formatted_reasons,
            "checklist": checklist,
            "tri": tri,
            "history": history,
            "token": active_token,
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

    # Invalidate token only if closing as non-priority
    if action == "bukan_prioritas":
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
    from app.config import get_settings
    if not get_settings().demo_mode:
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
    _: None = Depends(require_login),
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

    # Auto-sync: Find any active companies in this officer's region that have no active case_file
    unassigned_comps_res = await session.execute(
        select(Company)
        .where(
            Company.status == "active",
            Company.region_id == current_officer.region_id,
            ~Company.company_id.in_(
                select(CaseFile.company_id).where(CaseFile.status.in_(["pending", "assigned", "snoozed"]))
            )
        )
    )
    unassigned_comps = unassigned_comps_res.scalars().all()

    for u_comp in unassigned_comps:
        t_plain = secrets.token_urlsafe(16)
        t_hash = sha256(t_plain.encode()).hexdigest()
        c_id = f"case-{uuid4().hex[:8]}"
        d_val = date.today() + timedelta(days=2)
        
        rs_res = await session.execute(
            select(RiskScore).where(RiskScore.company_id == u_comp.company_id).order_by(RiskScore.as_of.desc()).limit(1)
        )
        rs_obj = rs_res.scalar_one_or_none()
        r_val = rs_obj.risk if rs_obj else 60.0

        session.add(CaseFile(
            case_id=c_id,
            company_id=u_comp.company_id,
            status="assigned",
            priority=float(r_val * 100),
            assigned_officer=current_officer.officer_id,
            due_date=d_val,
            reasons_json=json.dumps([{
                "code": "AUTO_SYNC_REGISTRATION",
                "title": f"Triangulasi Integritas Iuran — {u_comp.name}",
                "detail": f"Entitas aktif di wilayah {u_comp.region_id} ({u_comp.registered_headcount} pekerja). Siap diverifikasi silang.",
                "weight": 0.6,
            }]),
            checklist_json=json.dumps([
                {"item": "Periksa daftar gaji (payroll) asli dan bandingkan dengan data kepesertaan", "required": True},
                {"item": "Konfirmasi potongan 1% pekerja pada slip gaji", "required": True},
                {"item": "Verifikasi keabsahan jumlah tenaga kerja aktif di lapangan", "required": True},
            ]),
            access_token_hash=t_hash,
            token_expires=datetime.utcnow() + timedelta(hours=24),
        ))

    if unassigned_comps:
        await session.commit()

    # Query assigned cases for this officer
    # Join only the latest RiskScore per company; RiskScore keeps one row per as_of date,
    # so a plain join would repeat each case once per historical score.
    latest_score = (
        select(RiskScore.company_id, func.max(RiskScore.as_of).label("max_as_of"))
        .group_by(RiskScore.company_id)
        .subquery()
    )
    cases_res = await session.execute(
        select(CaseFile, Company, Region, RiskScore)
        .join(Company, Company.company_id == CaseFile.company_id)
        .join(Region, Region.region_id == Company.region_id)
        .outerjoin(latest_score, latest_score.c.company_id == Company.company_id)
        .outerjoin(
            RiskScore,
            (RiskScore.company_id == latest_score.c.company_id)
            & (RiskScore.as_of == latest_score.c.max_as_of),
        )
        .where(CaseFile.assigned_officer == current_officer.officer_id)
        .order_by(CaseFile.priority.desc())
    )
    rows = cases_res.all()

    today_str = datetime.now().strftime("%A, %d %B %Y")
    cases_data = []
    total_est_mid = 0
    cases_without_estimate = 0
    high_priority_count = 0

    from app.config import get_rules
    rules = get_rules()
    risk_labels = rules.risk_labels
    visit_hours = float(rules.planner["default_visit_hours"])
    open_cases = 0

    for case, comp, reg, score in rows:
        # Ensure an active token exists for each case so the officer can click it
        token_plain = secrets.token_urlsafe(16)
        token_hash = sha256(token_plain.encode()).hexdigest()
        case.access_token_hash = token_hash
        case.token_expires = datetime.utcnow() + timedelta(hours=24)

        # No score yet means no estimate and no risk label; never substitute a made-up figure.
        est_mid = score.est_mid if score and score.est_mid else None
        if est_mid is None:
            cases_without_estimate += 1
        else:
            total_est_mid += est_mid

        if score is None:
            risk_label = "Belum dinilai"
        elif score.risk >= risk_labels["high"]:
            risk_label = "Tinggi"
            high_priority_count += 1
        elif score.risk >= risk_labels["medium"]:
            risk_label = "Sedang"
        else:
            risk_label = "Rendah"

        if case.status in ("pending", "assigned", "snoozed"):
            open_cases += 1

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
            "est_mid_fmt": _format_idr(est_mid) if est_mid is not None else None,
            "top_signal_text": top_reason_text,
            "due_date": case.due_date.strftime("%d %b %Y") if case.due_date else "Hari Ini",
            "status": case.status,
            "token": token_plain,
        })

    await session.commit()

    # Planned workload: open cases x the planner's default visit duration (rules.yaml).
    hours_used = round(open_cases * visit_hours, 1)

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
            "cases_without_estimate": cases_without_estimate,
            "hours_used": hours_used,
            "visit_hours": visit_hours,
            "auth_enabled": auth_enabled(),
        },
    )


# ---------------------------------------------------------------------------
# Worker Payslip Verification Web Demo (/verify)
# ---------------------------------------------------------------------------

@router.get("/verify", response_class=HTMLResponse)
async def slip_verify_page(request: Request):
    """Worker Payslip Verification Web Demo page."""
    return templates.TemplateResponse(request=request, name="verify.html", context={})


class VerifyEvaluateRequest(BaseModel):
    base_wage: int = Field(ge=0, le=1_000_000_000)
    allowance: int = Field(default=0, ge=0, le=1_000_000_000)
    deduction: int = Field(ge=0, le=1_000_000_000)
    recorded_wage: Optional[int] = Field(default=None, ge=0, le=1_000_000_000)
    umk: int = Field(default=3_000_000, ge=0, le=1_000_000_000)
    is_registered: bool = True
    is_remitted: Optional[bool] = None


@router.post("/verify/evaluate")
async def slip_verify_evaluate(payload: VerifyEvaluateRequest):
    """
    Stateless rule-engine run for the /verify lab. Stores nothing and needs no API key:
    it only does arithmetic on the numbers the visitor typed in.
    """
    from app.rules.engine import BPJSRecord, verdict_slip
    from app.slip.schema import PayslipExtraction

    fixed = [{"label": "Tunjangan Tetap", "amount": payload.allowance, "confidence": 1.0}] if payload.allowance else []
    extraction = PayslipExtraction.model_validate({
        "is_payslip": True,
        "earnings": [{"label": "Gaji Pokok", "amount": payload.base_wage, "confidence": 1.0}, *fixed],
        "deductions": [{"label": "BPJS Kesehatan", "amount": payload.deduction, "confidence": 1.0}],
    })
    result = verdict_slip(
        extraction,
        BPJSRecord(
            recorded_wage_base=payload.recorded_wage or None,
            is_remitted=payload.is_remitted,
            is_registered=payload.is_registered,
        ),
        payload.umk,
    )
    return {
        "verdicts": result.codes,
        "expected_deduction": result.expected_deduction,
        "actual_deduction": result.actual_deduction,
        "wage_base_used": result.wage_base_used,
        "explanation": result.explanation,
        "rule_version": result.rule_version,
    }
