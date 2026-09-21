"""
Slip verification flow handler — the core bot interaction.

Flow:
  photo received → preprocess → Gemini extract → validate → rule engine → verdict message
  Ambiguous component → ask clarification → re-run rule engine
  [Laporkan] → save WorkerReport anonymously
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from uuid import uuid4

from telegram import Update
from telegram.ext import ContextTypes

from app.audit import write_audit
from app.bot.keyboards import clarification_keyboard, verdict_action_keyboard, work_status_keyboard, skip_keyboard
from app.config import get_rules, get_settings
from app.db.models import SlipCheck, WorkerConsent, WorkerReport
from app.db.session import AsyncSessionLocal
from app.rules.engine import BPJSRecord, Verdict, verdict_slip
from app.slip.gemini_client import GeminiExtractionError, extract_payslip
from app.slip.preprocessor import preprocess
from app.slip.validator import validate_extraction

logger = logging.getLogger(__name__)

_CONSENT_VERSION = "1.0"
_MAX_CHECKS_PER_HOUR = get_rules().rate_limit.get("slip_checks_per_hour", 10)


def _worker_hash(telegram_user_id: int) -> str:
    """One-way hash of Telegram user ID — this is the only identifier stored."""
    salt = get_settings().secret_key.get_secret_value()[:16]
    return hashlib.sha256(f"{salt}:{telegram_user_id}".encode()).hexdigest()


def _format_verdict_message(result, company_name: str) -> str:
    codes = result.codes

    if Verdict.NEEDS_REVIEW in codes:
        return (
            f"Saya belum yakin.\n{result.explanation}\n\n"
            "Silakan kirim ulang foto yang lebih jelas, atau koreksi angka yang ditampilkan."
        )

    lines = []

    if Verdict.OK in codes:
        lines += [
            f"Potongan JKN di slip: Rp{result.actual_deduction:,}",
            f"Sesuai perhitungan (upah dasar Rp{result.wage_base_used:,} × 1%)",
            f"Semua selaras di {company_name}. Terima kasih sudah mengecek.",
        ]
        return "\n".join(lines)

    if result.actual_deduction is not None:
        lines.append(f"Potongan JKN di slip: Rp{result.actual_deduction:,}")
    if Verdict.DEDUCTION_MISSING in codes:
        lines.append("Potongan BPJS Kesehatan tidak ditemukan di slip")
    if Verdict.DEDUCTION_LOW in codes and result.expected_deduction:
        lines.append(f"Seharusnya ±Rp{result.expected_deduction:,} (upah dasar Rp{result.wage_base_used:,})")
    if Verdict.WAGE_BASE_MISMATCH in codes:
        lines.append(f"Upah dasar tercatat di BPJS mungkin lebih rendah dari yang sebenarnya")
    if Verdict.NOT_REMITTED in codes:
        lines.append("Setoran bulan ini: belum tercatat")
    if Verdict.NOT_REGISTERED in codes:
        lines.append("Anda tampaknya belum terdaftar di perusahaan ini")
    if Verdict.BELOW_UMK in codes:
        lines.append(f"Upah dasar terindikasi di bawah UMK")
    if Verdict.DEDUCTION_HIGH in codes:
        lines.append(f"Potongan lebih besar dari seharusnya (bukan indikasi masalah utama)")

    lines.append("")
    lines.append("Artinya: data di BPJS mungkin belum sesuai dengan slip Anda.")

    return "\n".join(lines)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for all photo messages from workers."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    worker_hash = _worker_hash(user_id)

    async with AsyncSessionLocal() as session:
        # Check consent
        consent = await session.get(WorkerConsent, worker_hash)
        if not consent or consent.revoked_at:
            await update.message.reply_text(
                "Harap setujui ketentuan privasi terlebih dahulu. Ketik /start"
            )
            return

        # Rate limiting: check how many checks in the last hour
        from sqlalchemy import select, func
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=1)
        count_result = await session.execute(
            select(func.count(SlipCheck.check_id))
            .where(SlipCheck.worker_hash == worker_hash)
            .where(SlipCheck.created_at >= cutoff)
        )
        check_count = count_result.scalar() or 0
        if check_count >= _MAX_CHECKS_PER_HOUR:
            await update.message.reply_text(
                f"Anda sudah melakukan {_MAX_CHECKS_PER_HOUR} cek dalam 1 jam terakhir. "
                "Silakan coba lagi nanti."
            )
            return

        await update.message.reply_text("Sedang membaca slip Anda... (≤30 detik)")
        start_ms = int(time.monotonic() * 1000)

        # Download photo (highest resolution)
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await photo_file.download_to_drive(tmp_path)
            raw_bytes = open(tmp_path, "rb").read()
        finally:
            os.unlink(tmp_path)  # Photo deleted immediately — never touches disk long-term

        # Preprocess
        try:
            jpeg_bytes, _ = preprocess(raw_bytes)
        except ValueError as exc:
            await update.message.reply_text(f"Gagal memproses gambar: {exc}\nCoba kirim foto slip yang lebih jelas.")
            return

        # Gemini extraction
        try:
            extraction = await extract_payslip(jpeg_bytes)
        except GeminiExtractionError:
            await update.message.reply_text(
                "Sedang ada gangguan pembacaan. Silakan coba lagi dalam beberapa menit."
            )
            return

        # Post-extraction validation
        validation = validate_extraction(extraction)

        # Get company from user session context
        company_id = context.user_data.get("selected_company_id", "UNKNOWN")
        company_name = context.user_data.get("selected_company_name", "perusahaan Anda")

        # Get BPJS synthetic record (for demo: use company_id to fetch from DB)
        bpjs_record = await _get_bpjs_record(session, company_id, worker_hash)
        umk = await _get_umk(session, company_id)

        rules = get_rules()
        result = verdict_slip(extraction, bpjs_record, umk, rules)
        latency_ms = int(time.monotonic() * 1000) - start_ms

        # If ambiguous components, ask clarification (handled by separate callback handler)
        if result.has_ambiguous_components:
            context.user_data["pending_extraction"] = extraction.model_dump_json()
            context.user_data["pending_company_id"] = company_id
            context.user_data["pending_umk"] = umk
            await update.message.reply_text(
                f"ℹ️ Saya belum yakin ada komponen: "
                f"{', '.join(repr(l) for l in result.ambiguous_labels)}\n"
                "Apakah tunjangan tersebut dibayar tetap setiap bulan?",
                reply_markup=clarification_keyboard("pending"),
            )
            return

        # Save SlipCheck (no photo, only structured data)
        period = "2026-09"  # TODO: extract from extraction.period
        if extraction.period and extraction.period.month and extraction.period.year:
            period = f"{extraction.period.year}-{extraction.period.month:02d}"

        check = SlipCheck(
            check_id=str(uuid4()),
            worker_hash=worker_hash,
            company_id=company_id,
            period=period,
            extracted_json=extraction.model_dump_json(),
            verdict_codes=json.dumps(result.codes),
            expected_deduction=result.expected_deduction,
            actual_deduction=result.actual_deduction,
            implied_wage_base=result.implied_wage_base,
            rule_version=result.rule_version,
            latency_ms=latency_ms,
        )
        session.add(check)
        await write_audit(
            session, actor=worker_hash, action="SLIP_CHECK",
            object_ref=company_id,
            meta={"verdicts": result.codes, "latency_ms": latency_ms},
        )
        await session.commit()

        context.user_data["last_check_id"] = check.check_id

        verdict_text = _format_verdict_message(result, company_name)
        is_problem = Verdict.OK not in result.codes and Verdict.NEEDS_REVIEW not in result.codes

        reply_markup = verdict_action_keyboard(check.check_id) if is_problem else None
        await update.message.reply_text(verdict_text, reply_markup=reply_markup)

        # P1: ask optional status question after verdict
        if is_problem:
            await update.message.reply_text(
                "Boleh tanya satu hal lagi? (opsional, tetap anonim)\n"
                "Status kerja Anda di perusahaan ini?",
                reply_markup=work_status_keyboard(),
            )


async def handle_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle [Laporkan (anonim)] callback."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    _, _, check_id = query.data.split(":", 2)
    user_id = query.from_user.id if query.from_user else 0
    worker_hash = _worker_hash(user_id)

    async with AsyncSessionLocal() as session:
        check = await session.get(SlipCheck, check_id)
        if not check:
            await query.edit_message_text("Laporan tidak ditemukan.")
            return

        # Save anonymous report — no worker_hash in worker_report table
        verdicts = json.loads(check.verdict_codes) if check.verdict_codes else []
        category = verdicts[0] if verdicts else "UNKNOWN"

        report = WorkerReport(
            report_id=str(uuid4()),
            check_id=check_id,
            company_id=check.company_id,
            category=category,
        )
        session.add(report)
        await write_audit(
            session, actor="anonymous", action="REPORT",
            object_ref=check.company_id,
            meta={"category": category},
        )
        await session.commit()

    await query.edit_message_text(
        "Terima kasih. Laporan Anda dicatat secara anonim.\n"
        "Petugas hanya akan bertindak jika ada bukti tambahan dari data. "
        "Nama Anda tidak pernah dibagikan. Anda bisa menghapus data kapan saja dengan /hapus."
    )


async def _get_bpjs_record(session, company_id: str, worker_hash: str) -> BPJSRecord:
    """Fetch synthetic BPJS enrollment + billing for this worker's company."""
    from app.db.models import Billing, Enrollment
    from sqlalchemy import select
    from datetime import date

    # In synthetic mode, we use the company's average or first enrollment
    enrollment_result = await session.execute(
        select(Enrollment.reported_wage_base, Enrollment.registered_status)
        .where(Enrollment.company_id == company_id)
        .limit(1)
    )
    enrollment = enrollment_result.first()

    period_str = datetime.now().strftime("%Y-%m")
    billing_result = await session.execute(
        select(Billing.paid_amount, Billing.billed_amount)
        .where(Billing.company_id == company_id)
        .where(Billing.period == period_str)
        .limit(1)
    )
    billing = billing_result.first()

    is_remitted = None
    if billing:
        is_remitted = billing.paid_amount >= billing.billed_amount * 0.95

    return BPJSRecord(
        recorded_wage_base=enrollment.reported_wage_base if enrollment else None,
        is_remitted=is_remitted,
        is_registered=enrollment is not None,
    )


async def _get_umk(session, company_id: str) -> int:
    """Get UMK for the company's region."""
    from app.db.models import Company, Region
    from sqlalchemy import select

    result = await session.execute(
        select(Region.umk)
        .join(Company, Company.region_id == Region.region_id)
        .where(Company.company_id == company_id)
        .limit(1)
    )
    row = result.first()
    return row.umk if row else 3_000_000  # fallback UMK
