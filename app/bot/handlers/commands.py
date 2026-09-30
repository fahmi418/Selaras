"""
Bot command handlers: /start, /status, /hapus, /privasi, /bantuan.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from uuid import uuid4

from telegram import Update
from telegram.ext import ContextTypes

from app.audit import write_audit
from app.bot.keyboards import consent_keyboard
from app.config import get_settings
from app.db.models import SlipCheck, WorkerConsent, WorkerReport
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_CONSENT_VERSION = "1.0"
_PRIVACY_TEXT = """<b>Kebijakan Privasi Selaras</b>

• Foto slip dihapus segera setelah dibaca. Kami tidak menyimpan gambar.
• Data yang disimpan: angka yang terekstrak, hasil verifikasi — tanpa nama atau NIK Anda.
• Laporan ke BPJS bersifat anonim. Perusahaan Anda tidak akan tahu siapa yang melapor.
• Anda bisa menghapus seluruh data Anda kapan saja dengan perintah /hapus.
• Mode demo: semua data adalah sintetis (contoh). Jangan kirim slip asli Anda.

Dengan menekan [Setuju & Lanjut] Anda menyetujui ketentuan ini (versi {version}).
"""

_ONBOARDING_TEXT = """Halo! Saya Selaras.

Kirim foto slip gaji Anda, saya cek apakah potongan BPJS Kesehatan Anda wajar dan tercatat. Hanya ±30 detik.

Keamanan: Foto slip dihapus permanen setelah dibaca. Laporan bersifat anonim dan identitas Anda terlindungi.

Catatan Mode Demo: Gunakan slip contoh, bukan slip asli Anda."""


def _worker_hash(telegram_user_id: int) -> str:
    salt = get_settings().secret_key.get_secret_value()[:16]
    return hashlib.sha256(f"{salt}:{telegram_user_id}".encode()).hexdigest()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    await update.message.reply_text(
        _ONBOARDING_TEXT,
        reply_markup=consent_keyboard(),
    )


async def cmd_bantuan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Perintah yang tersedia:\n"
        "/start — mulai / onboarding\n"
        "/cek — kirim slip gaji untuk dicek\n"
        "/status — riwayat cek Anda\n"
        "/privasi — baca kebijakan privasi\n"
        "/hapus — hapus semua data Anda"
    )


async def cmd_privasi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_html(
        _PRIVACY_TEXT.format(version=_CONSENT_VERSION),
        reply_markup=consent_keyboard(),
    )


async def cmd_hapus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete all data associated with this worker (GDPR-equivalent right)."""
    if not update.message or not update.effective_user:
        return

    worker_hash = _worker_hash(update.effective_user.id)

    async with AsyncSessionLocal() as session:
        # Collect check_ids belonging to this worker
        from sqlalchemy import select, delete

        check_ids_result = await session.execute(
            select(SlipCheck.check_id).where(SlipCheck.worker_hash == worker_hash)
        )
        check_ids = [r[0] for r in check_ids_result.all()]

        # Delete slip checks
        await session.execute(
            delete(SlipCheck).where(SlipCheck.worker_hash == worker_hash)
        )

        # Revoke consent
        consent = await session.get(WorkerConsent, worker_hash)
        if consent:
            consent.revoked_at = datetime.utcnow()

        # Audit trail: record deletion without referencing the hash
        await write_audit(
            session, actor="system", action="DATA_DELETE",
            object_ref="worker",
            meta={"checks_deleted": len(check_ids)},
        )
        await session.commit()

    await update.message.reply_text(
        "Semua data Anda telah dihapus. Terima kasih telah mempercayai Selaras.\n"
        "Anda dapat memulai lagi kapan saja dengan /start."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    worker_hash = _worker_hash(update.effective_user.id)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        results = await session.execute(
            select(
                SlipCheck.period,
                SlipCheck.verdict_codes,
                SlipCheck.created_at,
            )
            .where(SlipCheck.worker_hash == worker_hash)
            .order_by(SlipCheck.created_at.desc())
            .limit(5)
        )
        rows = results.all()

    if not rows:
        await update.message.reply_text("Belum ada riwayat cek slip. Kirim foto slip untuk memulai!")
        return

    import json
    lines = ["<b>Riwayat cek Anda (5 terakhir):</b>"]
    for period, verdict_codes_json, created_at in rows:
        codes = json.loads(verdict_codes_json or "[]")
        code_str = ", ".join(codes) if codes else "–"
        date_str = created_at.strftime("%d %b %Y") if created_at else "–"
        lines.append(f"• {period} ({date_str}): {code_str}")

    await update.message.reply_html("\n".join(lines))


async def handle_consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()

    action = query.data.split(":")[1] if query.data else ""
    worker_hash = _worker_hash(query.from_user.id)

    if action == "yes":
        async with AsyncSessionLocal() as session:
            existing = await session.get(WorkerConsent, worker_hash)
            if existing:
                existing.revoked_at = None
                existing.consent_version = _CONSENT_VERSION
                existing.consented_at = datetime.utcnow()
            else:
                session.add(WorkerConsent(
                    worker_hash=worker_hash,
                    consent_version=_CONSENT_VERSION,
                    consented_at=datetime.utcnow(),
                ))
            await session.commit()

        await query.edit_message_text(
            "Terima kasih. Sekarang kirim foto slip gaji Anda.\n\n"
            "Pertama, cari perusahaan Anda — ketik nama atau kode NPP:"
        )
        context.user_data["state"] = "SELECTING_COMPANY"

    elif action == "read":
        await query.message.reply_html(_PRIVACY_TEXT.format(version=_CONSENT_VERSION))

    elif action == "no":
        await query.edit_message_text(
            "Tidak masalah. Anda bisa kembali kapan saja dengan /start.\n"
            "Kami tidak menyimpan data Anda tanpa persetujuan."
        )


async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text search when user types company name or NPP."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if text.startswith("/"):
        return

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select, or_
        from app.db.models import Company
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        # Search by name or NPP (case-insensitive)
        query = select(Company).where(
            or_(
                Company.name.ilike(f"%{text}%"),
                Company.npp.ilike(f"%{text}%"),
            )
        ).limit(5)
        res = await session.execute(query)
        companies = res.scalars().all()

        if not companies:
            all_comp_res = await session.execute(select(Company).limit(4))
            sample_companies = all_comp_res.scalars().all()
            
            buttons = [
                [InlineKeyboardButton(f"{c.name} ({c.npp})", callback_data=f"company:select:{c.company_id}")]
                for c in sample_companies
            ]
            await update.message.reply_text(
                f"Perusahaan '{text}' tidak ditemukan di database contoh.\n\n"
                "Pilih dari daftar contoh berikut atau langsung kirimkan foto slip gaji Anda:",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
            return

        if len(companies) == 1:
            comp = companies[0]
            context.user_data["selected_company_id"] = comp.company_id
            context.user_data["selected_company_name"] = comp.name
            context.user_data["state"] = "READY_FOR_SLIP"
            await update.message.reply_text(
                f"Perusahaan dipilih: {comp.name} (NPP: {comp.npp})\n\n"
                "Silakan kirimkan foto slip gaji Anda sekarang untuk diperiksa."
            )
        else:
            buttons = [
                [InlineKeyboardButton(f"{c.name} ({c.npp})", callback_data=f"company:select:{c.company_id}")]
                for c in companies
            ]
            await update.message.reply_text(
                f"Ditemukan {len(companies)} perusahaan. Pilih yang sesuai:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )


async def handle_company_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback when user clicks a company button."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    _, _, company_id = query.data.split(":", 2)
    async with AsyncSessionLocal() as session:
        from app.db.models import Company
        comp = await session.get(Company, company_id)
        if comp:
            context.user_data["selected_company_id"] = comp.company_id
            context.user_data["selected_company_name"] = comp.name
            context.user_data["state"] = "READY_FOR_SLIP"
            await query.edit_message_text(
                f"Perusahaan dipilih: {comp.name} (NPP: {comp.npp})\n\n"
                "Silakan kirimkan foto slip gaji Anda sekarang untuk diperiksa."
            )
        else:
            await query.edit_message_text("Perusahaan tidak ditemukan. Silakan kirimkan foto slip gaji Anda.")
