"""
Portal Badan Usaha (HRD) — Horizon 2 Pilar 4: Proactive Self-Correction.

Perusahaan login dengan NPP melihat indikasi risiko berbasis data publik
(transparan: skor + atribusi penyumbang) dan simulator what-if deklarasi
perbaikan. Tidak ada data petugas internal yang bocor ke halaman ini.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import get_settings
from app.db.models import Company
from app.db.session import get_db
from app.risk.scoring import get_latest_score
from app.xai.attribution import signal_attribution
from app.xai.counterfactual import scenarios_for_company
from app.xai.whatif import SelfCorrectionDeclaration, what_if_for_company
from app.web.auth import _sign

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

HRD_COOKIE = "selaras_hrd"
_TTL_SECONDS = 3600 * 12


class HRDLoginRequired(Exception):
    def __init__(self) -> None:
        super().__init__("hrd session required")


def _make_session_value(npp: str) -> str:
    expires = int(time.time()) + _TTL_SECONDS
    return f"{expires}.{npp}." + _sign(f"hrd:{npp}:{expires}")


def hrd_npp_from_cookie(request: Request) -> Optional[str]:
    """NPP dari cookie sesi HRD bila tanda tangan & masa berlaku valid."""
    token = request.cookies.get(HRD_COOKIE)
    if not token or token.count(".") != 2:
        return None
    expires_str, npp, signature = token.split(".", 2)
    if not expires_str.isdigit() or int(expires_str) < time.time():
        return None
    if not hmac.compare_digest(signature, _sign(f"hrd:{npp}:{expires_str}")):
        return None
    return npp


def _set_session(response: RedirectResponse, npp: str) -> None:
    response.set_cookie(
        HRD_COOKIE,
        _make_session_value(npp),
        max_age=_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def require_hrd_session(request: Request, session: AsyncSession) -> Company:
    npp = hrd_npp_from_cookie(request)
    if npp is None:
        raise HRDLoginRequired()
    company = (await session.execute(
        select(Company).where(Company.npp == npp)
    )).scalar_one_or_none()
    if company is None:
        raise HRDLoginRequired()
    return company


def _fmt_money(value) -> str:
    value = int(value or 0)
    if value >= 1_000_000_000:
        return f"Rp{value / 1_000_000_000:.1f} miliar" if value % 1_000_000_000 else f"Rp{value // 1_000_000_000} miliar"
    if value >= 1_000_000:
        return f"Rp{value / 1_000_000:.1f} jt" if value % 1_000_000 else f"Rp{value // 1_000_000} jt"
    return f"Rp{value:,.0f}"


@router.get("/hrd", response_class=HTMLResponse)
async def hrd_login_page(request: Request):
    if hrd_npp_from_cookie(request):
        return RedirectResponse("/hrd/dashboard", status_code=303)
    return templates.TemplateResponse(request=request, name="hrd_login.html", context={"error": None})


@router.post("/hrd/login", response_class=HTMLResponse)
async def hrd_login_submit(request: Request, npp: str = Form(...), db: AsyncSession = Depends(get_db)):
    npp_clean = npp.strip()
    company = (await db.execute(
        select(Company).where(Company.npp == npp_clean)
    )).scalar_one_or_none()
    if company is None:
        return templates.TemplateResponse(
            request=request,
            name="hrd_login.html",
            context={"error": "NPP tidak ditemukan. Pastikan badan usaha telah terdaftar."},
            status_code=401,
        )
    response = RedirectResponse("/hrd/dashboard", status_code=303)
    _set_session(response, npp_clean)
    return response


@router.get("/hrd/logout")
async def hrd_logout():
    response = RedirectResponse("/hrd", status_code=303)
    response.delete_cookie(HRD_COOKIE, path="/")
    return response


@router.get("/hrd/dashboard", response_class=HTMLResponse)
async def hrd_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    company = await require_hrd_session(request, db)
    score = await get_latest_score(db, company.company_id)
    scenarios = await scenarios_for_company(db, company.company_id)

    attribution = []
    risk = None
    stored_signals = {}
    if score is not None:
        stored_signals = json.loads(score.signals_json) if score.signals_json else {}
        attribution = signal_attribution(stored_signals)
        risk = score.risk

    return templates.TemplateResponse(
        request=request,
        name="hrd_dashboard.html",
        context={
            "company": company,
            "risk": risk,
            "risk_label": _risk_feasibility(risk),
            "attribution": attribution,
            "scenarios": scenarios["scenarios"] if scenarios else [],
            "signals": stored_signals,
        },
    )


def _risk_feasibility(risk) -> str:
    from app.config import get_rules

    labels = get_rules().risk_labels
    if risk is None:
        return "Belum ada indikasi"
    if risk >= labels["high"]:
        return "Indikasi risiko TINGGI"
    if risk >= labels["medium"]:
        return "Indikasi risiko SEDANG"
    return "Indikasi risiko RENDAH"


@router.post("/hrd/what-if", response_class=HTMLResponse)
async def hrd_what_if(
    request: Request,
    declared_new_workers: int = Form(0),
    declare_benchmark_wage: bool = Form(False),
    declare_remit_fixed: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    company = await require_hrd_session(request, db)
    declaration = SelfCorrectionDeclaration(
        declared_new_workers=max(0, declared_new_workers),
        declare_benchmark_wage=declare_benchmark_wage,
        declare_remit_fixed=declare_remit_fixed,
    )

    no_snapshot_message = None
    result = await what_if_for_company(db, company.company_id, declaration)
    if result is None:
        no_snapshot_message = "Simulator belum tersedia karena snapshot data belum terbit."

    if result is not None or no_snapshot_message is None:
        await write_audit(
            db,
            actor=f"company:{company.company_id}",
            action="SELF_CORRECTION_DECLARE",
            object_ref=company.company_id,
            meta={
                "declared_new_workers": declaration.declared_new_workers,
                "declare_benchmark_wage": declaration.declare_benchmark_wage,
                "declare_remit_fixed": declaration.declare_remit_fixed,
                "risk_delta": (result or {}).get("risk_delta"),
            },
        )
        await db.commit()

    score = await get_latest_score(db, company.company_id)
    stored_signals = json.loads(score.signals_json) if score is not None and score.signals_json else {}

    return templates.TemplateResponse(
        request=request,
        name="hrd_dashboard.html",
        context={
            "company": company,
            "risk": score.risk if score is not None else None,
            "risk_label": _risk_feasibility(score.risk if score is not None else None),
            "attribution": signal_attribution(stored_signals),
            "scenarios": [],
            "signals": stored_signals,
            "what_if": result,
            "no_snapshot": no_snapshot_message,
        },
    )


templates.env.globals["fmt_money"] = _fmt_money
