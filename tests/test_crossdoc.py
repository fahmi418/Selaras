"""Uji nyandingan lint sumber (Pilar 2 Horizon 3): toleransi rules, eskalasi kritis."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import get_rules
from app.documents.matching import match_documents, summarize_findings
from app.documents.schema import (
    BankStatementRecord,
    BankTransfer,
    CrossDocBundle,
    PayrollRecord,
    PayrollRow,
    PPH21Record,
    PayslipRecord,
)


@pytest.fixture()
def rules():
    return get_rules()


def _bundle(pph21=None, payroll=None, statement=None, slips=(), period="2026-09"):
    return CrossDocBundle(
        company_id="cX", period=period, pph21=pph21, payroll=payroll,
        bank_statement=statement, slips=list(slips),
    )


def test_missing_bundle_yields_incomplete_notice(rules):
    findings = match_documents(_bundle(), rules=rules)
    assert [f.code for f in findings] == ["X0_INCOMPLETE"]
    assert findings[0].severity == "info"


def test_pph21_within_tolerance_is_info(rules):
    upah = 5_000_000
    bundle = _bundle(
        pph21=PPH21Record(period="2026-09", gross_wage=upah),
        payroll=PayrollRecord(period="2026-09", rows=[
            PayrollRow(gross_wage=upah * (1 + rules.crossdoc["wage_tolerance_pct"] * 0.8)),
        ]),
    )
    findings = match_documents(bundle, rules=rules)
    assert findings[0].severity == "info"


def test_pph21_beyond_tolerance_is_critical(rules):
    upah = 5_000_000
    spread = upah * (1 + rules.crossdoc["wage_tolerance_pct"] * 3)  # jauh di atas toleransi
    bundle = _bundle(
        pph21=PPH21Record(period="2026-09", gross_wage=upah),
        payroll=PayrollRecord(period="2026-09", rows=[PayrollRow(gross_wage=spread)]),
    )
    findings = match_documents(bundle, rules=rules)
    x1 = next(f for f in findings if f.code == "X1_PPH21_PAYROLL")
    assert x1.severity == "critical"


def test_slip_not_in_payroll_is_warning(rules):
    ded = 50_000
    bundle = _bundle(
        payroll=PayrollRecord(period="2026-09", rows=[
            PayrollRow(jkn_deduction=ded * 0.5),   # setengah — jauh dari slip
        ]),
        slips=[PayslipRecord(period="2026-09", jkn_deduction=ded)],
    )
    findings = match_documents(bundle, rules=rules)
    x2 = next(f for f in findings if f.code == "X2_SLIP_PAYROLL")
    assert x2.severity == "warning"


def test_bank_flow_mismatch_is_critical_and_escalates(rules):
    gross = 5_000_000
    ded = 50_000
    net = gross - ded
    transferred = net * (1 - rules.crossdoc["bank_transfer_tolerance_pct"]) - 500_000
    bundle = _bundle(
        pph21=PPH21Record(period="2026-09", gross_wage=gross * 1.6),  # critical kedua
        payroll=PayrollRecord(period="2026-09", rows=[PayrollRow(gross_wage=gross, jkn_deduction=ded)]),
        statement=BankStatementRecord(
            period="2026-09", transfers=[BankTransfer(amount=round(transferred, 2), memo="KPI")],
        ),
    )
    findings = match_documents(bundle, rules=rules)
    summary = summarize_findings(findings, rules)
    assert summary["critical"] == 2
    assert summary["escalate"] is True
    assert summary["escalation_threshold"] == int(rules.crossdoc["critical_discrepancies"])


def test_incomplete_bundle_does_not_escalate(rules):
    summary = summarize_findings(match_documents(_bundle(), rules=rules), rules)
    assert summary["escalate"] is False


def test_bundle_requires_company_and_period():
    with pytest.raises(ValidationError):
        CrossDocBundle(company_id="only-company")
