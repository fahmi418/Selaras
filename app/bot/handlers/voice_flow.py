"""
Multimodal Voice PANDAWA — bot voice-note flow (Horizon 3 Pilar 2).

Flow:
  voice note received → consent gate → rate limit (voice_checks_per_hour)
  → transcribe (VoiceTranscriptService) → redact PII → clip
  → classify W1/W3/W4 (deterministic) → WorkerReport (anonim)
  → upsert kasus aktif + audit VOICE_REPORT
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, func
from telegram import Update
from telegram.ext import ContextTypes

from app.audit import write_audit
from app.bot.handlers.slip_flow import _worker_hash
from app.cases.service import boosted_priority, find_open_case, upsert_active_case
from app.config import get_rules
from app.copilot.bap import TranscriptService
from app.db.models import AuditLog, Company, Officer, WorkerConsent, WorkerReport
from app.db.session import AsyncSessionLocal
from app.utils import utcnow
from app.voice.service import classify_complaint, clip_transcript, redact, reconcile_with_slip

logger = logging.getLogger(__name__)

_MAX_VOICE_PER_HOUR = get_rules().rate_limit.get("voice_checks_per_hour", 3)
_VOICE_ACTIONS = ("VOICE_REPORT", "REPORT")


async def _voice_count_last_hour(session, actor: str) -> int:
    cutoff = utcnow() - timedelta(hours=1)
    result = await session.execute(
        select(func.count(AuditLog.id))
        .where(AuditLog.actor == actor)
        .where(AuditLog.action.in_(_VOICE_ACTIONS))
        .where(AuditLog.ts >= cutoff)
    )
    return int(result.scalar() or 0)


async def handle_voice_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for voice messages from workers (PANDAWA analisis spontanitas)."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    worker_hash = _worker_hash(user_id)
    rules = get_rules()

    async with AsyncSessionLocal() as session:
        consent = await session.get(WorkerConsent, worker_hash)
        if not consent or consent.revoked_at:
            await update.message.reply_text(
                "Harap setujui ketentuan privasi terlebih dahulu. Ketik /start"
            )
            return

        count = await _voice_count_last_hour(session, f"worker:{worker_hash}")
        if count >= _MAX_VOICE_PER_HOUR:
            await update.message.reply_text(
                f"Anda sudah mengirim {_MAX_VOICE_PER_HOUR} pesan suara dalam 1 jam terakhir. "
                "Silakan coba lagi nanti."
            )
            return

        company_id = (context.user_data or {}).get("selected_company_id")
        if not company_id:
            await update.message.reply_text(
                "Pilih perusahaan dulu: kirim nama perusahaan tempat Anda bekerja, "
                "lalu pilih dari daftar yang muncul."
            )
            return

        await update.message.reply_text("Sedang mendengarkan pesan suara Anda... (≤30 detik)")

        voice = update.message.voice
        if not voice:
            await update.message.reply_text("Maaf, format audio tidak dikenali.")
            return

        try:
            file_obj = await voice.get_file()
            audio = await file_obj.download_as_bytearray()
        except Exception:
            logger.exception("Gagal mengunduh voice note")
            await update.message.reply_text("Gagal mengunduh rekaman. Coba kirim ulang.")
            return

        service = TranscriptService()
        try:
            transcript = await service.transcribe("voice.oga", bytes(audio), rules=rules)
        except Exception:
            logger.exception("Transkripsi voice note gagal")
            await update.message.reply_text("Gagal memproses rekaman. Coba kirim ulang.")
            return

        clean = clip_transcript(redact(transcript, rules=rules), rules=rules)
        category, keyword_hits = classify_complaint(clean, rules=rules)

        comp = await session.get(Company, company_id)
        if not comp:
            await update.message.reply_text("Perusahaan tidak ditemukan. Pilih ulang perusahaan Anda.")
            return

        report_id = str(uuid4())
        last_check_id = (context.user_data or {}).get("last_check_id")
        report = WorkerReport(
            report_id=report_id,
            check_id=last_check_id or report_id,
            company_id=comp.company_id,
            category=category,
            note=clean,
        )
        session.add(report)

        verdict_codes: list[str] = []
        if last_check_id:
            from app.db.models import SlipCheck

            check = await session.get(SlipCheck, last_check_id)
            if check and check.verdict_codes:
                try:
                    verdict_codes = json.loads(check.verdict_codes)
                except (TypeError, json.JSONDecodeError):
                    verdict_codes = []

        reconciliation_notes = reconcile_with_slip(category, verdict_codes, rules=rules)

        comp_res = await session.execute(
            select(Company).where(Company.company_id == comp.company_id)
        )
        company_row = comp_res.scalar_one_or_none()

        report_signal = {
            "code": "WORKER_REPORT_SIGNAL",
            "title": f"Laporan Voice Pekerja ({category})",
            "detail": f"Pekerja melaporkan keluhan lewat pesan suara ({category}).",
            "evidence": " ".join(reconciliation_notes) or "Transkrip diredaksi PII dideteksi.",
            "source": "Voice Note PANDAWA",
            "weight": 0.9,
        }

        officer = (await session.execute(
            select(Officer).where(Officer.region_id == company_row.region_id).limit(1)
        )).scalar_one_or_none()
        if officer is None:
            officer = (await session.execute(select(Officer).limit(1))).scalar_one_or_none()

        open_case = await find_open_case(session, company_row.company_id)
        await upsert_active_case(
            session,
            company_id=company_row.company_id,
            reasons=[report_signal],
            checklist=[
                {"item": "Cocokkan keluhan suara pekerja dengan vonis slip terkini", "required": True},
                {"item": "Verifikasi bukti setor iuran dari perusahaan", "required": True},
                {"item": "Wawancara pekerja sampel (wawancara terpisah dari HRD)", "required": False},
            ],
            priority=boosted_priority(open_case.priority if open_case else None),
            officer_id=officer.officer_id if officer else None,
            prepend_reasons=open_case is not None,
        )

        await write_audit(
            session,
            actor=f"worker:{worker_hash}",
            action="VOICE_REPORT",
            object_ref=company_row.company_id,
            meta={"category": category, "keywords": keyword_hits, "report_id": report_id},
        )
        await session.commit()

    reply = [f"Keluhan Anda tercatat — kategori: {category}."]
    reply += [f"Nota: {note}" for note in reconciliation_notes[:2]]
    reply.append("Terima kasih. Laporan Anda anonim; identitas tidak pernah dibagikan.")
    await update.message.reply_text("\n".join(reply))
