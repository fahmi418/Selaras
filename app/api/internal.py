"""
Internal and Admin API endpoints.

Internal endpoints (/api/*) — authenticated with INTERNAL_API_KEY header.
Admin endpoints (/admin/*) — authenticated with ADMIN_API_KEY header.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import get_rules, get_settings
from app.db.models import CaseFile, Company, Officer, RiskScore
from app.db.session import get_db
from app.rules.engine import BPJSRecord, verdict_slip
from app.slip.schema import PayslipExtraction

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def require_internal(x_api_key: str = Header(alias="X-API-Key")) -> None:
    settings = get_settings()
    if x_api_key != settings.internal_api_key.get_secret_value():
        raise HTTPException(status_code=403, detail="Invalid API key.")


async def require_admin(x_api_key: str = Header(alias="X-API-Key")) -> None:
    settings = get_settings()
    if x_api_key != settings.admin_api_key.get_secret_value():
        raise HTTPException(status_code=403, detail="Invalid admin key.")


# ---------------------------------------------------------------------------
# Internal API
# ---------------------------------------------------------------------------

@router.get("/api/queue", dependencies=[Depends(require_internal)])
async def get_queue(
    officer_id: str = Query(...),
    as_of: Optional[date] = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Return the current case queue for an officer."""
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
    """Return latest risk score + signals for a company."""
    result = await session.execute(
        select(RiskScore)
        .where(RiskScore.company_id == company_id)
        .order_by(RiskScore.as_of.desc())
        .limit(1)
    )
    score = result.scalar_one_or_none()

    if not score:
        raise HTTPException(status_code=404, detail="Skor tidak ditemukan untuk perusahaan ini.")

    from app.config import get_rules
    rules = get_rules()
    labels = rules.risk_labels
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
    extraction: dict               # raw PayslipExtraction dict
    company_id: str
    umk: int = 3_000_000
    recorded_wage_base: Optional[int] = None
    is_remitted: Optional[bool] = None
    is_registered: bool = True


@router.post("/api/slip/verify", dependencies=[Depends(require_internal)])
async def verify_slip(payload: SlipVerifyRequest):
    """
    Verify a pre-extracted slip against the rule engine.
    Useful for testing and frontend integration.
    """
    try:
        extraction = PayslipExtraction.model_validate(payload.extraction)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    bpjs = BPJSRecord(
        recorded_wage_base=payload.recorded_wage_base,
        is_remitted=payload.is_remitted,
        is_registered=payload.is_registered,
    )

    rules = get_rules()
    result = verdict_slip(extraction, bpjs, payload.umk, rules)

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


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

@router.post("/admin/synth/reset", dependencies=[Depends(require_admin)])
async def reset_synthetic_data(session: AsyncSession = Depends(get_db)):
    """Drop and reload all synthetic data from generator with fixed seed."""
    from synth.generator import generate_and_load
    from app.config import get_settings

    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Reset only available in demo mode.")

    await write_audit(session, actor="admin", action="DATA_RESET", object_ref="all")
    await session.commit()

    count = await generate_and_load(session, seed=settings.synth_seed)
    return {"status": "ok", "companies_loaded": count}


@router.post("/admin/jobs/run/{job}", dependencies=[Depends(require_admin)])
async def run_job(job: str, session: AsyncSession = Depends(get_db)):
    """Manually trigger a scheduled job."""
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
    elif job == "scoring":
        from app.risk.scoring import run_scoring
        n = await run_scoring(session, as_of=today)
        return {"scored": n}
    elif job == "queue":
        from app.queue.planner import build_daily_queue
        assignments = await build_daily_queue(session, target_date=today)
        return {"assignments": {k: len(v) for k, v in assignments.items()}}
    elif job == "morning_message":
        from app.queue.notifier import send_morning_messages
        sent = await send_morning_messages(session, target_date=today)
        return {"messages_sent": sent}
    return {}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "selaras-backend"}
