"""
SQLAlchemy ORM models — mirrors PRD §16 data model.

Design decisions:
- All PKs are TEXT (UUID4 or prefixed slugs) for portability across SQLite/Postgres.
- `worker_hash` is isolated: it appears in `slip_check` and `worker_consent` only.
  The `worker_report` table deliberately omits it so officer/dashboard layers cannot
  join back to identify reporters.
- Images are never stored; only structured JSON fields are persisted.
- Audit log uses integer PK for fast append; all other tables use UUID strings.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Reference / benchmark tables
# ---------------------------------------------------------------------------


class Region(Base):
    __tablename__ = "region"

    region_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    province: Mapped[str] = mapped_column(String(128))
    city: Mapped[str] = mapped_column(String(128))
    umk: Mapped[int] = mapped_column(BigInteger)        # Upah Minimum Kab/Kota (Rp)
    umk_year: Mapped[int] = mapped_column(Integer)

    companies: Mapped[list["Company"]] = relationship(back_populates="region")
    officers: Mapped[list["Officer"]] = relationship(back_populates="region")


class SectorBenchmark(Base):
    __tablename__ = "sector_benchmark"
    __table_args__ = (UniqueConstraint("sector", "region_id"),)

    sector: Mapped[str] = mapped_column(String(64), primary_key=True)
    region_id: Mapped[str] = mapped_column(String(64), ForeignKey("region.region_id"), primary_key=True)
    median_wage: Mapped[int] = mapped_column(BigInteger)
    p25_wage: Mapped[int] = mapped_column(BigInteger)
    p75_wage: Mapped[int] = mapped_column(BigInteger)


# ---------------------------------------------------------------------------
# Synthetic BPJS data
# ---------------------------------------------------------------------------


class Company(Base):
    __tablename__ = "company"

    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    npp: Mapped[str] = mapped_column(String(32), unique=True)          # Nomor Pendaftaran Perusahaan
    name: Mapped[str] = mapped_column(String(256))
    sector: Mapped[str] = mapped_column(String(64))
    region_id: Mapped[str] = mapped_column(String(64), ForeignKey("region.region_id"))
    size_bucket: Mapped[str] = mapped_column(String(16))               # micro | small | medium | large
    est_headcount: Mapped[int] = mapped_column(Integer)                # perkiraan jumlah pekerja
    registered_headcount: Mapped[int] = mapped_column(Integer)         # terdaftar di BPJS
    founded_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Hashed/normalized keys for graph feature — never real PII
    owner_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    address_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    phone_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | inactive

    region: Mapped["Region"] = relationship(back_populates="companies")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="company")
    billings: Mapped[list["Billing"]] = relationship(back_populates="company")
    risk_scores: Mapped[list["RiskScore"]] = relationship(back_populates="company")
    case_files: Mapped[list["CaseFile"]] = relationship(back_populates="company")
    worker_reports: Mapped[list["WorkerReport"]] = relationship(back_populates="company")


class Enrollment(Base):
    """Upah terdaftar per pekerja pseudonim di BPJS (sintetis)."""

    __tablename__ = "enrollment"

    enrollment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), index=True)
    worker_pid: Mapped[str] = mapped_column(String(64))        # pseudonim — bukan NIK asli
    reported_wage_base: Mapped[int] = mapped_column(BigInteger)
    since: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    registered_status: Mapped[str] = mapped_column(String(32))  # karyawan_tetap | kontrak | mitra | ...

    company: Mapped["Company"] = relationship(back_populates="enrollments")


class Billing(Base):
    """Riwayat tagihan dan setoran iuran per perusahaan per periode."""

    __tablename__ = "billing"
    __table_args__ = (UniqueConstraint("company_id", "period"),)

    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)   # "YYYY-MM"
    billed_amount: Mapped[int] = mapped_column(BigInteger)
    paid_amount: Mapped[int] = mapped_column(BigInteger)
    paid_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="billings")


# ---------------------------------------------------------------------------
# Worker side — privacy-by-design
# ---------------------------------------------------------------------------


class WorkerConsent(Base):
    __tablename__ = "worker_consent"

    worker_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    consent_version: Mapped[str] = mapped_column(String(16))
    consented_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    slip_checks: Mapped[list["SlipCheck"]] = relationship(
        back_populates="worker", foreign_keys="SlipCheck.worker_hash"
    )


class SlipCheck(Base):
    """
    Hasil satu cek slip gaji. Tidak menyimpan foto — hanya field terstruktur.
    `verdict_codes` adalah JSON array string, e.g. '["DEDUCTION_LOW","WAGE_BASE_MISMATCH"]'.
    """

    __tablename__ = "slip_check"
    __table_args__ = (
        UniqueConstraint("worker_hash", "company_id", "period", name="uq_worker_company_period"),
    )

    check_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_hash: Mapped[str] = mapped_column(
        String(128), ForeignKey("worker_consent.worker_hash"), index=True
    )
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), index=True)
    period: Mapped[str] = mapped_column(String(7))                  # "YYYY-MM"
    extracted_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verdict_codes: Mapped[str] = mapped_column(Text, default="[]") # JSON array
    expected_deduction: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    actual_deduction: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    implied_wage_base: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    rule_version: Mapped[str] = mapped_column(String(32))
    # P1 fields
    self_reported_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    self_reported_start_ym: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # "YYYY-MM"
    self_reported_end_ym: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    worker: Mapped["WorkerConsent"] = relationship(back_populates="slip_checks")


class WorkerReport(Base):
    """
    Laporan anonim dari pekerja. Sengaja tidak memiliki FK ke worker_hash
    agar layer petugas tidak bisa menelusuri identitas pelapor.
    Petugas hanya mengakses view agregat v_company_reports.
    """

    __tablename__ = "worker_report"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    check_id: Mapped[str] = mapped_column(String(36), index=True)  # referensi ke slip_check, tanpa FK
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), index=True)
    category: Mapped[str] = mapped_column(String(32))  # kode sinyal: W1, W3, W4, ...
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="worker_reports")


# ---------------------------------------------------------------------------
# Risk & cases
# ---------------------------------------------------------------------------


class CompanyFeatures(Base):
    """Snapshot fitur harian per perusahaan — dipakai oleh IsolationForest."""

    __tablename__ = "company_features"
    __table_args__ = (UniqueConstraint("company_id", "as_of"),)

    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    feature_json: Mapped[str] = mapped_column(Text)  # JSON dict of all features


class RiskScore(Base):
    __tablename__ = "risk_score"
    __table_args__ = (UniqueConstraint("company_id", "as_of"),)

    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    rule_score: Mapped[float] = mapped_column(Float)
    anomaly_score: Mapped[float] = mapped_column(Float)
    risk: Mapped[float] = mapped_column(Float)                 # final 0–100
    signals_json: Mapped[str] = mapped_column(Text)            # JSON: {code: contribution}
    est_low: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    est_mid: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    est_high: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    p_valid: Mapped[float] = mapped_column(Float, default=0.0) # calibrated P(temuan valid)
    model_version: Mapped[str] = mapped_column(String(32))

    company: Mapped["Company"] = relationship(back_populates="risk_scores")


class Officer(Base):
    __tablename__ = "officer"

    officer_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    region_id: Mapped[str] = mapped_column(String(64), ForeignKey("region.region_id"))
    daily_hours: Mapped[float] = mapped_column(Float, default=6.0)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    region: Mapped["Region"] = relationship(back_populates="officers")
    case_files: Mapped[list["CaseFile"]] = relationship(back_populates="assigned_officer_obj")


class CaseFile(Base):
    """
    Kasus aktif yang masuk antrian kunjungan petugas.
    `access_token_hash` adalah SHA-256 dari token acak 128-bit;
    token aslinya hanya dikirim sekali lewat pesan Telegram.
    """

    __tablename__ = "case_file"

    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("company.company_id"), index=True)
    status: Mapped[str] = mapped_column(
        String(24), default="pending", index=True
    )  # pending | assigned | visited | closed | snoozed
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    assigned_officer: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("officer.officer_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")    # JSON list of reason cards
    checklist_json: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of checklist items
    access_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="case_files")
    assigned_officer_obj: Mapped[Optional["Officer"]] = relationship(back_populates="case_files")
    outcomes: Mapped[list["VisitOutcome"]] = relationship(back_populates="case")


class VisitOutcome(Base):
    __tablename__ = "visit_outcome"

    outcome_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("case_file.case_id"), index=True)
    result: Mapped[str] = mapped_column(String(24))  # TERBUKTI | TIDAK_TERBUKTI | PERLU_TINDAK_LANJUT
    confirmed_modus: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # "1","2","3",...
    found_amount: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    case: Mapped["CaseFile"] = relationship(back_populates="outcomes")


# ---------------------------------------------------------------------------
# Audit (append-only from application code)
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(128))    # worker_hash | officer_id | "system" | "admin"
    action: Mapped[str] = mapped_column(String(64))    # SLIP_CHECK | REPORT | CASE_ACTION | etc.
    object: Mapped[str] = mapped_column(String(128))   # company_id | case_id | ...
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
