"""
Mesin nyandingan lint sumber (Pilar 2 — Cross-Document Matching).

100% deterministik: membandingkan nilai terstruktur hasil ekstraksi VLM
dengan toleransi dari rules.yaml. Tidak ada penilaian LLM di tahap ini —
hasilnya bukti angka yang bisa ditelusuri petugas.

X1 PPh21↔payroll: upah kotor.  X2 slip↔payroll: potongan JKN.
X3 rekening koran↔payroll: arus keluar vs neto.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from app.config import RulesConfig, get_rules
from app.documents.schema import CrossDocBundle


class Discrepancy(BaseModel):
    code: str                    # mis: X1_PPH21_PAYROLL
    severity: str                # info | warning | critical
    title: str
    evidence: str


def _fmt(number: Optional[float]) -> str:
    return f"Rp{number:,.0f}" if number else "Rp0"


def match_documents(bundle: CrossDocBundle, rules: RulesConfig | None = None) -> list[Discrepancy]:
    """Bandingkan pasangan dokumen; keluarkan temuan bertoleransi rules.yaml."""
    rules = rules or get_rules()
    wage_tol = float(rules.crossdoc["wage_tolerance_pct"])
    bank_tol = float(rules.crossdoc["bank_transfer_tolerance_pct"])
    findings: list[Discrepancy] = []

    # X1 — PPh 21 vs payroll: upah kotor per periode.
    if bundle.pph21 is not None and bundle.pph21.gross_wage is not None:
        payroll_rows = [
            row for row in (bundle.payroll.rows if bundle.payroll else [])
            if row.gross_wage is not None
        ]
        if payroll_rows:
            best = min(
                payroll_rows,
                key=lambda row: abs(row.gross_wage - bundle.pph21.gross_wage),
            )
            delta = abs(best.gross_wage - bundle.pph21.gross_wage)
            tolerance = wage_tol * max(best.gross_wage, bundle.pph21.gross_wage)
            if delta > tolerance:
                findings.append(Discrepancy(
                    code="X1_PPH21_PAYROLL",
                    severity="critical",
                    title="Upah PPh 21 tak selaras dengan payroll",
                    evidence=(
                        f"PPh 21 {_fmt(bundle.pph21.gross_wage)} vs payroll terdekat "
                        f"{_fmt(best.gross_wage)} (selisih {_fmt(delta)} > toleransi "
                        f"{wage_tol:.0%})."
                    ),
                ))
            else:
                findings.append(Discrepancy(
                    code="X1_PPH21_PAYROLL",
                    severity="info",
                    title="Upah PPh 21 selaras payroll",
                    evidence=f"Selisih {_fmt(delta)} dalam toleransi {wage_tol:.0%}.",
                ))

    # X2 — slip pekerja vs payroll: potongan JKN per slip.
    if bundle.payroll and bundle.slips:
        ded_rows = [row for row in bundle.payroll.rows if row.jkn_deduction is not None]
        for slip in bundle.slips:
            if slip.jkn_deduction is None or not ded_rows:
                continue
            candidates = [abs(row.jkn_deduction - slip.jkn_deduction) for row in ded_rows]
            best_delta = min(candidates)
            tolerance = 0.02 * max(slip.jkn_deduction, 1)
            if best_delta > tolerance:
                findings.append(Discrepancy(
                    code="X2_SLIP_PAYROLL",
                    severity="warning",
                    title="Potongan JKN slip pekerja tidak ada di payroll",
                    evidence=(
                        f"Slip {_fmt(slip.jkn_deduction)} tanpa pasangan payroll "
                        f"(delta terdekat {_fmt(best_delta)})."
                    ),
                ))

    # X3 — rekening koran vs payroll neto harapan.
    if bundle.bank_statement and bundle.payroll:
        inferred_net: list[float] = []
        for row in bundle.payroll.rows:
            if row.gross_wage is not None and row.jkn_deduction is not None:
                inferred_net.append(row.gross_wage - row.jkn_deduction)
        transfers_total = sum(
            t.amount or 0 for t in bundle.bank_statement.transfers
        )
        if inferred_net and transfers_total:
            expected = sum(inferred_net)
            delta = abs(expected - transfers_total)
            tolerance = bank_tol * expected
            if delta > tolerance:
                findings.append(Discrepancy(
                    code="X3_BANK_PAYROLL",
                    severity="critical",
                    title="Setoran rekening koran tak selaras payroll",
                    evidence=(
                        f"Transfer teramati {_fmt(transfers_total)} vs ekspektasi neto "
                        f"{_fmt(expected)} (toleransi {bank_tol:.0%})."
                    ),
                ))
            else:
                findings.append(Discrepancy(
                    code="X3_BANK_PAYROLL",
                    severity="info",
                    title="Arus keluar rekening selaras payroll",
                    evidence=f"Delta {_fmt(delta)} dalam toleransi {bank_tol:.0%}.",
                ))

    if not findings:
        findings.append(Discrepancy(
            code="X0_INCOMPLETE",
            severity="info",
            title="Dokumen belum lengkap untuk nyandingan",
            evidence="Butuh minimal satu pasangan dokumen bernilai kuantitatif.",
        ))
    return findings


def summarize_findings(findings: list[Discrepancy], rules: RulesConfig) -> dict[str, Any]:
    """Ringkasan + keputusan eskalasi bila temuan kritis melewati ambang rules."""
    critical = [f for f in findings if f.severity == "critical"]
    threshold = int(rules.crossdoc["critical_discrepancies"])
    return {
        "findings": [f.model_dump() for f in findings],
        "info": sum(1 for f in findings if f.severity == "info"),
        "warning": sum(1 for f in findings if f.severity == "warning"),
        "critical": len(critical),
        "escalate": len(critical) >= threshold,
        "escalation_threshold": threshold,
    }


async def escalate_findings(
    session: Any,
    company_id: str,
    period: str,
    summary: dict[str, Any],
    rules: RulesConfig,
) -> bool:
    """Buat/tungkaskan kasus aktif bila temuan kritis memenuhi ambang; False bila tidak."""
    if not summary.get("escalate"):
        return False

    from app.cases.service import baseline_priority, upsert_active_case

    critical_codes = [
        f["code"] for f in summary["findings"] if f["severity"] == "critical"
    ]
    await upsert_active_case(
        session,
        company_id=company_id,
        reasons=[
            {
                "code": "CROSSDOC_MISMATCH",
                "title": "Selisih lintas dokumen kritis",
                "detail": f"Dokumen periode {period} berbenturan ({', '.join(critical_codes)}).",
                "evidence": "; ".join(
                    f["evidence"] for f in summary["findings"] if f["severity"] == "critical"
                ),
                "source": "Cross-Document Matching",
                "weight": 1.0,
            }
        ],
        checklist=[
            {"item": f"Periksa dokumen terkait {period} dan minta sumber asli", "required": True},
        ],
        priority=baseline_priority(rules),
        prepend_reasons=True,
    )
    return True
