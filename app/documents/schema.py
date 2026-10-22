"""
Skema dokumen lint sumber (Pilar 2 — Cross-Document VLM Matching).

Ekstraksi datang DARI model falsafah luar melalui extractor; modul ini hanya
menyediakan kontrak data (pydantic) dan validasi dasar. Meet semua field
opsional agar dokumen penalca (OCR gagal di satu field) tetap bisa dianalisis.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PPH21Record(BaseModel):
    """Sorotan bukti potong PPh 21 untuk satu periode satu pekerja."""

    period: str                      # YYYY-MM
    gross_wage: Optional[float] = None
    tax_deduction: Optional[float] = None
    employee_name_initials: Optional[str] = None   # tanpa PII — inisial saja


class PayrollRow(BaseModel):
    worker_initials: Optional[str] = None
    gross_wage: Optional[float] = None
    jkn_deduction: Optional[float] = None


class PayrollRecord(BaseModel):
    period: str
    rows: list[PayrollRow] = Field(default_factory=list)


class BankTransfer(BaseModel):
    amount: Optional[float] = None
    day: Optional[int] = None        # 1..31 — tanpa tanggal penuh agar tahan OCR
    memo: Optional[str] = None


class BankStatementRecord(BaseModel):
    period: str
    counterparty_hint: Optional[str] = None        # nama penerima resmi
    transfers: list[BankTransfer] = Field(default_factory=list)


class PayslipRecord(BaseModel):
    """Sorotan ringkas slip gaji pekerja (bukan full PayslipExtraction)."""

    period: str
    gross_wage: Optional[float] = None
    jkn_deduction: Optional[float] = None


DocumentPayload = PPH21Record | PayrollRecord | BankStatementRecord | PayslipRecord


class CrossDocBundle(BaseModel):
    """Kumpulan dokumen satu perusahaan pada satu periode arahan."""

    company_id: str
    period: str
    pph21: Optional[PPH21Record] = None
    payroll: Optional[PayrollRecord] = None
    bank_statement: Optional[BankStatementRecord] = None
    slips: list[PayslipRecord] = Field(default_factory=list)
