"""
Service pembuatan kasus — titik tunggal untuk membuat/memperbarui CaseFile.

Dipakai planner, portal petugas, API admin, dan alur laporan bot agar id kasus,
prioritas, tenggat, dan token akses konsisten seluruh sistem.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import date, timedelta
from hashlib import sha256
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_rules, get_settings
from app.db.models import CaseFile
from app.utils import utcnow

_OPEN_STATUSES = ("pending", "assigned", "snoozed")

# Label sinyal tunggal: dipakai planner (alasan kasus) dan copilot (briefing).
SIGNAL_LABELS = {
    "C1_under_reporting": ("Indikasi Upah Dilaporkan Lebih Rendah", "Data upah terdaftar vs UMK/sektor"),
    "C2_headcount_gap": ("Selisih Jumlah Pekerja", "Estimasi headcount vs yang terdaftar di BPJS"),
    "C3_remit_inconsistent": ("Setoran Tidak Konsisten", "Riwayat pembayaran iuran belakangan"),
    "C4_uniform_wage": ("Upah Seragam Mencurigakan", "Semua pekerja terdaftar persis di UMK"),
    "C5_churn": ("Churn Pekerja Tidak Wajar", "Mutasi keluar-masuk menjelang periode audit"),
    "C6_worker_reports": ("Laporan Pekerja Berulang", "Temuan pekerja via kanal anonim"),
}


async def find_open_case(session: AsyncSession, company_id: str) -> Optional[CaseFile]:
    """Kasus terbuka terbaru untuk sebuah perusahaan, bila ada."""
    result = await session.execute(
        select(CaseFile)
        .where(CaseFile.company_id == company_id)
        .where(CaseFile.status.in_(_OPEN_STATUSES))
        .order_by(desc(CaseFile.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def issue_access_token(case: CaseFile) -> str:
    """Tulis hash token akses pada kasus dan kembalikan token polos (untuk pesan)."""
    token = secrets.token_urlsafe(24)
    case.access_token_hash = sha256(token.encode()).hexdigest()
    case.token_expires = utcnow() + timedelta(hours=get_settings().case_token_ttl_hours)
    return token


def priority_floor_report(rules) -> float:
    """Prioritas minimum kasus yang tumbuh dari laporan pekerja."""
    return float(rules.cases["report_boost_risk_points"]) * float(rules.cases["priority_scale"])


def baseline_priority(rules) -> float:
    """Prioritas kasus tanpa skor risiko yang tersedia."""
    return float(rules.cases["baseline_risk_points"]) * float(rules.cases["priority_scale"])


def scale_priority(risk_points: float, rules) -> float:
    """Konversi poin risiko (0–100) ke angka prioritas antrean."""
    return float(risk_points) * float(rules.cases["priority_scale"])


async def upsert_active_case(
    session: AsyncSession,
    *,
    company_id: str,
    reasons: list[dict],
    checklist: list[dict],
    priority: float,
    officer_id: Optional[str] = None,
    due_days: Optional[int] = None,
    prepend_reasons: bool = False,
) -> CaseFile:
    """Buat atau pakai ulang kasus aktif dengan daftar alasan dan checklist.

    prepend_reasons=True menambahkan alasan baru ke depan daftar yang sudah ada
    (dipakai alur laporan pekerja agar riwayat alasan tidak hilang).
    """
    rules = get_rules()
    case = await find_open_case(session, company_id)
    existing_reasons: list[dict] = []

    if case is None:
        case = CaseFile(
            case_id=f"case-{uuid.uuid4().hex[:8]}",
            company_id=company_id,
        )
        session.add(case)
    elif prepend_reasons:
        existing_reasons = json.loads(case.reasons_json) if case.reasons_json else []

    case.status = "assigned"
    case.priority = float(priority)
    if officer_id:
        case.assigned_officer = officer_id

    days = int(due_days) if due_days is not None else int(rules.cases["due_days"])
    case.due_date = date.today() + timedelta(days=days)

    case.reasons_json = json.dumps([*reasons, *existing_reasons], ensure_ascii=False)
    case.checklist_json = json.dumps(checklist, ensure_ascii=False)
    await issue_access_token(case)
    return case


def boosted_priority(current_priority: Optional[float]) -> float:
    """Prioritas maksimum antara nilai kasus saat ini dan lantai laporan pekerja."""
    return max(float(current_priority or 0.0), priority_floor_report(get_rules()))
