"""
Notifier pesan pagi petugas — PRD §FR-D5.

Kirim daftar kasus prioritas per Telegram tiap pagi (jadwal dari morning_message
di rules.yaml). Bila skor risiko hari target belum ada, dipakai skor terakhir yang
tersedia; petugas tidak pernah menerima angka palsu.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.config import RulesConfig, get_rules, get_settings
from app.db.models import CaseFile, Company, Officer, RiskScore

logger = logging.getLogger(__name__)

_RISK_TAG = {"high": "[TINGGI]", "medium": "[SEDANG]", "low": "[RENDAH]"}


def risk_level(risk: float, rules: RulesConfig) -> str:
    labels = rules.risk_labels
    if risk >= labels["high"]:
        return "high"
    if risk >= labels["medium"]:
        return "medium"
    return "low"


def _fmt_idr(n: int) -> str:
    if n >= 1_000_000:
        return f"Rp{n // 1_000_000} jt"
    return f"Rp{n // 1_000} rb"


def _fmt_idr_range(est_low: Optional[int], est_high: Optional[int]) -> str:
    if est_low is None or est_high is None:
        return "Rp0"
    return f"{_fmt_idr(est_low)}\u2013{_fmt_idr(est_high)}"


async def send_morning_messages(
    session: AsyncSession,
    target_date: Optional[date] = None,
    bot: Optional[Bot] = None,
) -> int:
    """Kirim briefing pagi ke seluruh petugas berchat_id; jumlah pesan terkirim."""
    rules = get_rules()
    target_date = target_date or date.today()
    max_cases = int(rules.morning_message["max_cases_in_message"])

    if bot is None:
        from app.bot.gateway import get_bot
        bot = get_bot()
    if bot is None:
        logger.warning("notifier: bot Telegram belum dikonfigurasi; pesan pagi dilewati")
        return 0

    officers = (await session.execute(
        select(Officer).where(Officer.telegram_chat_id.isnot(None))
    )).scalars().all()

    latest_score = (
        select(RiskScore.company_id, func.max(RiskScore.as_of).label("max_as_of"))
        .where(RiskScore.as_of <= target_date)
        .group_by(RiskScore.company_id)
        .subquery()
    )

    sent = 0
    for officer in officers:
        rows = (await session.execute(
            select(CaseFile, Company, RiskScore)
            .join(Company, Company.company_id == CaseFile.company_id)
            .join(latest_score, latest_score.c.company_id == Company.company_id)
            .join(
                RiskScore,
                (RiskScore.company_id == latest_score.c.company_id)
                & (RiskScore.as_of == latest_score.c.max_as_of),
            )
            .where(CaseFile.assigned_officer == officer.officer_id)
            .where(CaseFile.due_date == target_date)
            .order_by(CaseFile.priority.desc())
            .limit(max_cases)
        )).all()

        if not rows:
            continue

        lines = [
            f"Selamat pagi, {officer.name}",
            f"Kapasitas hari ini: {officer.daily_hours:.0f} jam · Wilayah: {officer.region_id}",
            "",
            f"{len(rows)} kunjungan prioritas:",
        ]

        for i, (case, company, score) in enumerate(rows, 1):
            tag = _RISK_TAG[risk_level(score.risk, rules)]
            est_str = _fmt_idr_range(score.est_low, score.est_high)
            reasons = json.loads(case.reasons_json) if case.reasons_json else []
            reason_text = reasons[0]["title"] if reasons else "Anomali data terdeteksi"
            lines.append(f"{i}. {tag} {company.name} — {reason_text} · est. {est_str}")
            lines.append(f"   → /kasus {case.case_id[:8]}")

        lines.append("")
        lines.append("Catatan: Skor adalah indikasi awal, bukan bukti pelanggaran.")
        lines.append("Keputusan akhir tetap di tangan Anda (human-in-the-loop).")

        try:
            await bot.send_message(
                chat_id=officer.telegram_chat_id,
                text="\n".join(lines),
                parse_mode=None,
            )
            sent += 1
            logger.info("notifier: pesan pagi terkirim ke petugas %s", officer.officer_id)
        except Exception as exc:
            logger.error("notifier: gagal kirim ke %s: %s", officer.officer_id, exc)

    return sent
