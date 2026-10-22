"""
Halaman analitik internal (Fase 3 Horizon 3):

  /forecast  — Proyeksi beban antrean petugas (Pilar 5)
  /crossdoc  — Nyandingan dokumen lintas sumber (Pilar 2, kanal manual)
  /sandbox   — Pola fraud sintetis utk evaluasi rule engine (Pilar 4)

Semua halaman di balik require_login (porta pelulus sama dengan /portal).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import get_rules
from app.db.models import Company
from app.db.session import get_db
from app.documents.matching import (
    Discrepancy,
    escalate_findings,
    match_documents,
    summarize_findings,
)
from app.documents.schema import (
    BankStatementRecord,
    BankTransfer,
    CrossDocBundle,
    PayrollRecord,
    PayrollRow,
    PPH21Record,
    PayslipRecord,
)
from app.queue.forecast import forecast_workload
from app.web.auth import require_login

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)

_SEVERITY_STYLES = {"critical": "red", "warning": "orange", "info": "mint"}
_VOICE_CATEGORY_LABELS = {
    "W1": "Potongan Lebih Besar",
    "W3": "Upah Dilaporkan Rendah",
    "W4": "Iuran Tidak Disetor",
    "OTHER": "Keluhan Lain",
}


def _idr(val: float | int) -> str:
    return f"Rp {int(val):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# /forecast — Proyeksi beban kerja antrean


@router.get("/forecast", response_class=HTMLResponse)
async def forecast_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_login),
):
    result = await forecast_workload(session, rules=get_rules())
    capacity = float(result["capacity"]["daily_capacity_case"])

    rows = []
    max_expected = max(
        (row["expected_cases"] for row in result["forecast"]), default=0.0
    ) or 1.0
    for row in result["forecast"]:
        share = min(row["expected_cases"] / max_expected * 100, 100) if max_expected else 0
        load = row["expected_cases"] / capacity if capacity else None
        rows.append(
            {
                "date": row["date"],
                "weekday": row["weekday"],
                "expected": row["expected_cases"],
                "share_pct": round(share, 1),
                "over": load is not None and load > 1.0,
            }
        )

    context = {
        "fc": result,
        "rows": rows,
        "coverage_pct": (
            round(result["coverage_ratio"] * 100, 1) if result["coverage_ratio"] else None
        ),
        "capacity_display": capacity,
        "fmt": _idr,
    }
    return templates.TemplateResponse(request=request, name="forecast.html", context=context)


# ---------------------------------------------------------------------------
# /crossdoc — form + hasil nyandingan dokumen


@router.get("/crossdoc", response_class=HTMLResponse)
async def crossdoc_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_login),
):
    companies = (await session.execute(select(Company).order_by(Company.name))).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="crossdoc.html",
        context={"companies": companies, "result": None, "form": None},
    )


async def _company_choices(session: AsyncSession) -> list[Company]:
    return (await session.execute(select(Company).order_by(Company.name))).scalars().all()


def _numbers(raw: str) -> list[float]:
    values: list[float] = []
    for chunk in (raw or "").replace(",", " ").split():
        try:
            values.append(float(chunk))
        except ValueError:
            continue
    return values


def _build_findings_from_form(
    company_id: str,
    form: dict[str, str],
    rules,
) -> tuple[CrossDocBundle, list[Discrepancy]]:
    period = (form.get("period") or "2026-01").strip()
    payroll_gross = _numbers(form.get("payroll_gross", ""))
    payroll_jkn = _numbers(form.get("payroll_jkn", ""))
    rows = [
        PayrollRow(gross_wage=g, jkn_deduction=d)
        for g, d in zip(payroll_gross, payroll_jkn)
    ]
    slip_jkn = _numbers(form.get("slip_jkn", ""))
    pph21_gross = _numbers(form.get("pph21_gross", ""))
    transfers = _numbers(form.get("bank_amounts", ""))

    bundle = CrossDocBundle(
        company_id=company_id,
        period=period,
        payroll=PayrollRecord(period=period, rows=rows) if rows else None,
        slips=[
            PayslipRecord(period=period, jkn_deduction=val) for val in slip_jkn
        ],
        pph21=(
            PPH21Record(period=period, gross_wage=pph21_gross[0])
            if pph21_gross
            else None
        ),
        bank_statement=(
            BankStatementRecord(
                period=period,
                transfers=[BankTransfer(amount=val) for val in transfers],
            )
            if transfers
            else None
        ),
    )
    return bundle, match_documents(bundle, rules=rules)


@router.post("/crossdoc", response_class=HTMLResponse)
async def crossdoc_submit(
    request: Request,
    company_id: str = Form(...),
    period: str = Form(...),
    payroll_gross: str = Form(default=""),
    payroll_jkn: str = Form(default=""),
    slip_jkn: str = Form(default=""),
    pph21_gross: str = Form(default=""),
    bank_amounts: str = Form(default=""),
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_login),
):
    rules = get_rules()
    company = await session.get(Company, company_id)
    if company is None:
        return templates.TemplateResponse(
            request=request,
            name="crossdoc.html",
            context={
                "companies": await _company_choices(session),
                "result": None,
                "form": None,
                "error": "Perusahaan tidak dikenal. Pilih dari daftar.",
            },
            status_code=400,
        )

    bundle, findings = _build_findings_from_form(
        company_id,
        {
            "period": period,
            "payroll_gross": payroll_gross,
            "payroll_jkn": payroll_jkn,
            "slip_jkn": slip_jkn,
            "pph21_gross": pph21_gross,
            "bank_amounts": bank_amounts,
        },
        rules,
    )
    summary = summarize_findings(findings, rules)
    escalated = await escalate_findings(session, company_id, bundle.period, summary, rules)

    await write_audit(
        session,
        actor="officer-web",
        action="CROSSDOC_MATCH",
        object_ref=company_id,
        meta={"period": bundle.period, "critical": summary["critical"], "escalated": escalated},
    )
    await session.commit()

    finding_views = [
        {
            "code": f.code,
            "severity": f.severity,
            "style": _SEVERITY_STYLES.get(f.severity, "neutral"),
            "title": f.title,
            "evidence": f.evidence,
        }
        for f in findings
    ]

    return templates.TemplateResponse(
        request=request,
        name="crossdoc.html",
        context={
            "companies": await _company_choices(session),
            "result": {
                "company_name": company.name,
                "period": bundle.period,
                "findings": finding_views,
                "info": summary["info"],
                "warning": summary["warning"],
                "critical": summary["critical"],
                "escalated": escalated,
            },
            "form": {
                "company_id": company_id,
                "period": bundle.period,
                "payroll_gross": payroll_gross,
                "payroll_jkn": payroll_jkn,
                "slip_jkn": slip_jkn,
                "pph21_gross": pph21_gross,
                "bank_amounts": bank_amounts,
            },
        },
    )


# ---------------------------------------------------------------------------
# /sandbox — pola fraud sintetis


@router.get("/sandbox", response_class=HTMLResponse)
async def sandbox_page(
    request: Request,
    _: None = Depends(require_login),
):
    from synth.fraud_sandbox import dataset_summary

    rules = get_rules()
    patterns = dataset_summary(rules)
    for item in patterns:
        item["ramp_ratio"] = round(
            1 - (item["remit_ratio"] or 1.0), 3
        )
    return templates.TemplateResponse(
        request=request,
        name="sandbox.html",
        context={
            "patterns": patterns,
            "dataset_size": int(rules.sandbox["dataset_size"]),
        },
    )
