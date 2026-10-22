"""
API Internal dan Admin.

Endpoint /api/* diotentikasi header INTERNAL_API_KEY; /admin/* memakai ADMIN_API_KEY.
Perbandingan kunci dilakukan secara konstanta-waktu menghindari side-channel.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import date
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.cases.service import baseline_priority, upsert_active_case
from app.config import get_rules, get_settings
from app.copilot.bap import (
    BAPDraft,
    BAPGenerationError,
    BAPNotConfigured,
    TranscriptService,
    generate_bap,
)
from app.copilot.briefing import briefing_for_case
from app.db.models import Billing, CaseFile, Company, Enrollment, Officer, Region, RiskScore
from app.xai.attribution import signal_attribution
from app.xai.counterfactual import scenarios_for_company
from app.xai.whatif import SelfCorrectionDeclaration, what_if_for_company
from app.db.session import get_db
from app.rules.engine import BPJSRecord, verdict_slip
from app.slip.schema import PayslipExtraction
from app.utils import size_bucket_for

router = APIRouter()
logger = logging.getLogger(__name__)


async def require_internal(x_api_key: str = Header(alias="X-API-Key")) -> None:
    settings = get_settings()
    if not hmac.compare_digest(
        x_api_key.encode(), settings.internal_api_key.get_secret_value().encode()
    ):
        raise HTTPException(status_code=403, detail="Invalid API key.")


async def require_admin(x_api_key: str = Header(alias="X-API-Key")) -> None:
    settings = get_settings()
    if not hmac.compare_digest(
        x_api_key.encode(), settings.admin_api_key.get_secret_value().encode()
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key.")


@router.get("/api/queue", dependencies=[Depends(require_internal)])
async def get_queue(
    officer_id: str = Query(...),
    as_of: Optional[date] = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Antrean kasus aktif milik seorang petugas."""
    target = as_of or date.today()

    result = await session.execute(
        select(CaseFile, Company)
        .join(Company, Company.company_id == CaseFile.company_id)
        .where(CaseFile.assigned_officer == officer_id)
        .where(CaseFile.status.in_(["pending", "assigned"]))
        .order_by(CaseFile.priority.desc())
    )
    rows = result.all()

    return {
        "officer_id": officer_id,
        "date": target.isoformat(),
        "cases": [
            {
                "case_id": case.case_id,
                "company_name": company.name,
                "company_id": company.company_id,
                "priority": case.priority,
                "status": case.status,
                "due_date": case.due_date.isoformat() if case.due_date else None,
            }
            for case, company in rows
        ],
    }


@router.get("/api/company/{company_id}/risk", dependencies=[Depends(require_internal)])
async def get_company_risk(
    company_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Skor risiko terakhir beserta kontribusi sinyal per perusahaan."""
    result = await session.execute(
        select(RiskScore)
        .where(RiskScore.company_id == company_id)
        .order_by(RiskScore.as_of.desc())
        .limit(1)
    )
    score = result.scalar_one_or_none()

    if not score:
        raise HTTPException(status_code=404, detail="Skor tidak ditemukan untuk perusahaan ini.")

    labels = get_rules().risk_labels
    if score.risk >= labels["high"]:
        risk_label = "Tinggi"
    elif score.risk >= labels["medium"]:
        risk_label = "Sedang"
    else:
        risk_label = "Rendah"

    return {
        "company_id": company_id,
        "as_of": score.as_of.isoformat(),
        "risk": round(score.risk, 1),
        "risk_label": risk_label,
        "rule_score": round(score.rule_score, 3),
        "anomaly_score": round(score.anomaly_score, 3),
        "signals": json.loads(score.signals_json) if score.signals_json else {},
        "est_low": score.est_low,
        "est_mid": score.est_mid,
        "est_high": score.est_high,
        "p_valid": round(score.p_valid, 3),
        "model_version": score.model_version,
    }


class SlipVerifyRequest(BaseModel):
    extraction: dict
    company_id: str
    umk: int = Field(default_factory=lambda: get_rules().default_umk)
    recorded_wage_base: Optional[int] = None
    is_remitted: Optional[bool] = None
    is_registered: bool = True


@router.post("/api/slip/verify", dependencies=[Depends(require_internal)])
async def verify_slip(payload: SlipVerifyRequest):
    """Verifikasi slip hasil ekstraksi terhadap rule engine tanpa panggilan LLM."""
    try:
        extraction = PayslipExtraction.model_validate(payload.extraction)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    bpjs = BPJSRecord(
        recorded_wage_base=payload.recorded_wage_base,
        is_remitted=payload.is_remitted,
        is_registered=payload.is_registered,
    )
    result = verdict_slip(extraction, bpjs, payload.umk, get_rules())

    return {
        "verdicts": result.codes,
        "expected_deduction": result.expected_deduction,
        "actual_deduction": result.actual_deduction,
        "implied_wage_base": result.implied_wage_base,
        "wage_base_used": result.wage_base_used,
        "explanation": result.explanation,
        "confidence": result.confidence,
        "rule_version": result.rule_version,
    }


class CreateCompanyRequest(BaseModel):
    name: str
    npp: str
    sector: str = Field(default_factory=lambda: get_rules().company["default_sector"])
    region_id: str = Field(default_factory=lambda: get_rules().company["default_region_id"])
    registered_headcount: int = Field(
        ge=1, default_factory=lambda: get_rules().company["default_headcount"]
    )
    est_headcount: Optional[int] = None
    average_wage_base: int = Field(
        ge=0, default_factory=lambda: get_rules().company["default_average_wage"]
    )


@router.post("/admin/company", dependencies=[Depends(require_admin)])
async def create_company(
    payload: CreateCompanyRequest,
    session: AsyncSession = Depends(get_db),
):
    """Daftarkan badan usaha baru dengan baseline kepesertaan, tagihan, dan kasus verifikasi."""
    rules = get_rules()
    npp = payload.npp.strip()

    existing = await session.execute(select(Company).where(Company.npp == npp))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Badan Usaha dengan NPP {npp} sudah terdaftar.")

    region_result = await session.execute(select(Region).where(Region.region_id == payload.region_id))
    if not region_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Region tidak dikenal: {payload.region_id}")

    company_id = f"comp-{uuid4().hex[:8]}"
    est_count = payload.est_headcount or payload.registered_headcount

    company = Company(
        company_id=company_id,
        npp=npp,
        name=payload.name.strip(),
        sector=payload.sector.lower(),
        region_id=payload.region_id,
        size_bucket=size_bucket_for(payload.registered_headcount, rules),
        est_headcount=est_count,
        registered_headcount=payload.registered_headcount,
        status="active",
    )
    session.add(company)

    session.add(
        Enrollment(
            enrollment_id=f"enr-{uuid4().hex[:8]}",
            company_id=company_id,
            worker_pid=f"pid-{uuid4().hex[:6]}",
            reported_wage_base=payload.average_wage_base,
            registered_status="karyawan_tetap",
        )
    )

    billed = round(payload.registered_headcount * payload.average_wage_base * rules.contribution.total_rate)
    session.add(
        Billing(
            company_id=company_id,
            period=date.today().strftime("%Y-%m"),
            billed_amount=billed,
            paid_amount=billed,
            paid_at=date.today(),
        )
    )

    officer = (await session.execute(
        select(Officer).where(Officer.region_id == payload.region_id).limit(1)
    )).scalar_one_or_none()
    if officer is None:
        officer = (await session.execute(select(Officer).limit(1))).scalar_one_or_none()

    reasons = [
        {
            "code": "AUTO_SYNC_REGISTRATION",
            "title": f"Triangulasi Integritas Iuran — {company.name}",
            "detail": f"Entitas baru terdaftar ({payload.registered_headcount} pekerja). "
            "Siap diverifikasi silang dengan laporan slip pekerja dan UMK.",
            "evidence": f"NPP {company.npp} terdaftar dengan {payload.registered_headcount} pekerja.",
            "source": "Registrasi Admin",
            "weight": 0.6,
        }
    ]
    checklist = [
        {"item": "Periksa daftar gaji (payroll) asli dan bandingkan dengan data kepesertaan", "required": True},
        {"item": "Konfirmasi potongan 1% pekerja pada slip gaji", "required": True},
        {"item": "Verifikasi keabsahan jumlah tenaga kerja aktif di lapangan", "required": True},
    ]
    await upsert_active_case(
        session,
        company_id=company_id,
        reasons=reasons,
        checklist=checklist,
        priority=baseline_priority(rules),
        officer_id=officer.officer_id if officer else None,
    )

    await write_audit(
        session,
        actor="admin",
        action="COMPANY_CREATE",
        object_ref=company_id,
        meta={"name": company.name, "npp": company.npp},
    )
    await session.commit()

    return {"status": "ok", "company_id": company_id, "name": company.name, "npp": company.npp}


@router.post("/admin/synth/reset", dependencies=[Depends(require_admin)])
async def reset_synthetic_data(session: AsyncSession = Depends(get_db)):
    """Muat ulang seluruh data sintetis dari generator dengan seed tetap."""
    from synth.generator import generate_and_load

    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Reset only available in demo mode.")

    await write_audit(session, actor="admin", action="DATA_RESET", object_ref="all")
    await session.commit()

    count = await generate_and_load(session, seed=settings.synth_seed)
    return {"status": "ok", "companies_loaded": count}


@router.post("/admin/jobs/run/{job}", dependencies=[Depends(require_admin)])
async def run_job(job: str, session: AsyncSession = Depends(get_db)):
    """Picu job terjadwal secara manual (dilindungi kunci admin)."""
    allowed_jobs = {"features", "scoring", "queue", "morning_message"}
    if job not in allowed_jobs:
        raise HTTPException(status_code=400, detail=f"Unknown job: {job}. Allowed: {allowed_jobs}")

    result = await _dispatch_job(job, session)
    await write_audit(session, actor="admin", action="JOB_RUN", object_ref=job)
    return {"status": "ok", "job": job, "result": result}


async def _dispatch_job(job: str, session: AsyncSession) -> dict:
    today = date.today()
    if job == "features":
        from app.risk.features import build_features
        n = await build_features(session, as_of=today)
        return {"processed": n}
    if job == "scoring":
        from app.risk.scoring import run_scoring
        n = await run_scoring(session, as_of=today)
        return {"scored": n}
    if job == "queue":
        from app.queue.planner import build_daily_queue
        assignments = await build_daily_queue(session, target_date=today)
        return {"assignments": {k: len(v) for k, v in assignments.items()}}
    if job == "morning_message":
        from app.queue.notifier import send_morning_messages
        sent = await send_morning_messages(session, target_date=today)
        return {"messages_sent": sent}
    return {}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "selaras-backend"}


@router.get("/api/company/{company_id}/counterfactual", dependencies=[Depends(require_internal)])
async def get_company_counterfactual(company_id: str, session: AsyncSession = Depends(get_db)):
    """Kontrafaktual: risiko sesudah skenario perbaikan (Pilar 3 — XAI)."""
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Perusahaan tidak ditemukan.")
    scenarios = await scenarios_for_company(session, company_id)
    if scenarios is None:
        raise HTTPException(
            status_code=404, detail="Snapshot fitur belum tersedia untuk perusahaan ini."
        )
    return scenarios


class WhatIfResponse(BaseModel):
    declaration: SelfCorrectionDeclaration
    result: dict


@router.post("/api/company/{company_id}/what-if", dependencies=[Depends(require_internal)])
async def post_what_if(
    company_id: str,
    payload: SelfCorrectionDeclaration,
    session: AsyncSession = Depends(get_db),
):
    """Simulator what-if deklarasi mandiri badan usaha (Pilar 4)."""
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Perusahaan tidak ditemukan.")
    result = await what_if_for_company(session, company_id, payload)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Snapshot fitur belum tersedia untuk perusahaan ini."
        )
    await write_audit(
        session,
        actor="internal-api",
        action="WHATIF_SIMULATION",
        object_ref=company_id,
        meta={
            "declared_new_workers": payload.declared_new_workers,
            "declare_benchmark_wage": payload.declare_benchmark_wage,
            "declare_remit_fixed": payload.declare_remit_fixed,
            "risk_delta": result["risk_delta"],
        },
    )
    await session.commit()
    return result


@router.get("/api/company/{company_id}/attribution", dependencies=[Depends(require_internal)])
async def get_company_attribution(company_id: str, session: AsyncSession = Depends(get_db)):
    """Bukti atribusi kontribusi sinyal (Feature Attribution Dashboard)."""
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Perusahaan tidak ditemukan.")
    score = await get_latest_score_for(company_id, session)
    if score is None:
        raise HTTPException(status_code=404, detail="Skor tidak ditemukan untuk perusahaan ini.")
    signals = json.loads(score.signals_json) if score.signals_json else {}
    return {
        "company_id": company_id,
        "risk": score.risk,
        "rule_version": score.model_version,
        "attribution": signal_attribution(signals),
    }


async def get_latest_score_for(company_id: str, session: AsyncSession) -> Optional[RiskScore]:
    from app.risk.scoring import get_latest_score

    return await get_latest_score(session, company_id)


class BAPRequest(BaseModel):
    """Transkrip wawancara (opsional bila dikirim bersama file suara di masa depan)."""

    transcript: str


class BAPResponse(BaseModel):
    draft: BAPDraft


@router.get("/api/case/{case_id}/briefing", dependencies=[Depends(require_internal)])
async def get_case_briefing(case_id: str, session: AsyncSession = Depends(get_db)):
    """Briefing pra-audit untuk satu kasus (Pilar 1 — Pre-Audit Briefing)."""
    briefing = await briefing_for_case(session, case_id)
    if briefing is None:
        raise HTTPException(status_code=404, detail="Kasus tidak ditemukan.")
    return briefing


@router.post("/api/case/{case_id}/bap", response_model=BAPResponse, dependencies=[Depends(require_internal)])
async def draft_case_bap(
    case_id: str,
    payload: BAPRequest,
    session: AsyncSession = Depends(get_db),
):
    """Voice-to-BAP: transkrip wawancara -> draf BAP terstruktur (LLM di belakang antarmuka)."""
    case = await session.get(CaseFile, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Kasus tidak ditemukan.")

    company = await session.get(Company, case.company_id)
    company_name = company.name if company is not None else case.company_id

    try:
        if payload.transcript.strip():
            transcript = payload.transcript.strip()
        else:
            rules = get_rules()
            service = TranscriptService()
            transcript = await service.transcribe(filename="voice.ogg", audio=b"")
        draft = await generate_bap(
            case_id=case.case_id, company_name=company_name, transcript=transcript
        )
    except BAPNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except BAPGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await write_audit(
        session,
        actor="internal-api",
        action="BAP_DRAFT",
        object_ref=case.case_id,
        meta={"provider": draft.provider, "points": len(draft.points)},
    )
    await session.commit()
    return BAPResponse(draft=draft)


# ---------------------------------------------------------------------------
# Horizon 3 — Pilar 2: Cross-Document Matching endpoint (VLM belakang antarmuka)


class CrossDocBundleRequest(BaseModel):
    """Hamper dokumen lint sumber satu periode (hasil ekstraksi VLM di luar)."""

    period: str
    pph21: Optional[dict] = None
    payroll: Optional[dict] = None
    bank_statement: Optional[dict] = None
    slips: Optional[list[dict]] = None


@router.post("/api/company/{company_id}/crossdoc-match", dependencies=[Depends(require_internal)])
async def post_crossdoc_match(
    company_id: str,
    payload: CrossDocBundleRequest,
    session: AsyncSession = Depends(get_db),
):
    """Nyandingan lint sumber (PPh21/payroll/rekening koran/slip) → temuan + eskalasi kasus."""
    from app.documents.matching import escalate_findings, match_documents, summarize_findings
    from app.documents.schema import (
        BankStatementRecord,
        CrossDocBundle,
        PayrollRecord,
        PPH21Record,
        PayslipRecord,
    )

    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Perusahaan tidak ditemukan.")

    bundle = CrossDocBundle(
        company_id=company_id,
        period=payload.period,
        pph21=PPH21Record(**payload.pph21) if payload.pph21 else None,
        payroll=PayrollRecord(**payload.payroll) if payload.payroll else None,
        bank_statement=(
            BankStatementRecord(**payload.bank_statement) if payload.bank_statement else None
        ),
        slips=[PayslipRecord(**slip) for slip in (payload.slips or [])],
    )

    findings = match_documents(bundle)
    summary = summarize_findings(findings, get_rules())

    escalated = await escalate_findings(session, company_id, bundle.period, summary, get_rules())

    await write_audit(
        session,
        actor="internal-api",
        action="CROSSDOC_MATCH",
        object_ref=company_id,
        meta={"period": payload.period, "critical": summary["critical"], "escalated": escalated},
    )
    await session.commit()
    return {
        "company_id": company_id,
        "period": bundle.period,
        **summary,
        "escalated": escalated,
    }


# ---------------------------------------------------------------------------
# Horizon 3 — Pilar 4: Forecast workload antrean petugas


@router.get("/api/forecast/workload", dependencies=[Depends(require_internal)])
async def get_forecast_workload(
    session: AsyncSession = Depends(get_db),
    horizon_days: int = Query(default=0, ge=0, le=30),
):
    """Proyeksi beban kasus harian (moving average berbobot + faktor hari kerja)."""
    from app.queue.forecast import forecast_workload

    rules = get_rules()
    result = await forecast_workload(session, rules=rules)
    if horizon_days and horizon_days != int(rules.forecast["horizon_days"]):
        result["note"] = f"Horizon rules.yaml = {rules.forecast['horizon_days']} hari (parameter diabaikan)."
    await write_audit(
        session,
        actor="internal-api",
        action="FORECAST_VIEW",
        object_ref="all",
        meta={"expected_total": result["expected_total"]},
    )
    await session.commit()
    return result

