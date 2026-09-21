"""
Morning message notifier — PRD §FR-D5.

Sends a daily Telegram message to each officer with their prioritized case list.
Scheduled at 07:30 WIB via APScheduler; also triggerable via admin API for demo.
"""

from __future__ import annotations

import logging
from datetime import date

from telegram import Bot
from telegram.constants import ParseMode

from app.config import get_settings
from app.db.models import CaseFile, Company, Officer, RiskScore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_RISK_TAG = {
    "high": "[TINGGI]",
    "medium": "[SEDANG]",
    "low": "[RENDAH]",
}


def _risk_label(risk: float, rules_config) -> tuple[str, str]:
    labels = rules_config.risk_labels
    if risk >= labels["high"]:
        return "high", "Tinggi"
    if risk >= labels["medium"]:
        return "medium", "Sedang"
    return "low", "Rendah"


def _fmt_idr_range(est_low: int | None, est_high: int | None) -> str:
    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"Rp{n // 1_000_000} jt"
        return f"Rp{n // 1_000} rb"

    if est_low is None or est_high is None:
        return "Rp0"
    return f"{_fmt(est_low)}–{_fmt(est_high)}"


async def send_morning_messages(
    session: AsyncSession,
    target_date: Optional[date] = None,
    bot: Optional[Any] = None,
) -> int:
    """Send morning briefing message to all officers with assigned cases."""
    if target_date is None:
        target_date = date.today()

    rules = get_rules()
    settings = get_settings()
    base_url = settings.app_base_url

    # Lazy-import bot if not provided
    if bot is None:
        from app.bot.gateway import get_bot
        bot = get_bot()

    if bot is None:
        logger.warning("notifier: telegram bot not configured; skipping morning messages")
        return 0

    officers_result = await session.execute(
        select(Officer).where(Officer.telegram_chat_id.isnot(None))
    )
    officers = officers_result.scalars().all()

    sent = 0
    for officer in officers:
        cases_result = await session.execute(
            select(CaseFile, Company, RiskScore)
            .join(Company, Company.company_id == CaseFile.company_id)
            .join(
                RiskScore,
                (RiskScore.company_id == CaseFile.company_id)
                & (RiskScore.as_of == target_date),
            )
            .where(CaseFile.assigned_officer == officer.officer_id)
            .where(CaseFile.due_date == target_date)
            .order_by(CaseFile.priority.desc())
            .limit(rules.planner.get("max_cases_in_message", 5) if hasattr(rules, "planner") else 5)
        )
        rows = cases_result.all()

        if not rows:
            continue

        lines = [
            f"Selamat pagi, {officer.name}",
            f"Kapasitas hari ini: {officer.daily_hours:.0f} jam · Wilayah: {officer.region_id}",
            "",
            f"{len(rows)} kunjungan prioritas:",
        ]

        for i, (case, company, score) in enumerate(rows, 1):
            level_key, level_label = _risk_label(score.risk, rules)
            tag = _RISK_TAG[level_key]
            est_str = _fmt_idr_range(case.est_low if hasattr(case, "est_low") else score.est_low, score.est_high)

            import json
            reasons = json.loads(case.reasons_json) if case.reasons_json else []
            reason_text = reasons[0]["title"] if reasons else "Anomali data terdeteksi"

            lines.append(
                f"{i}. {tag} {company.name} — {reason_text} · est. {est_str}"
                f"\n   → /kasus {case.case_id[:8]}"
            )

        lines.append("")
        lines.append("Catatan: Skor adalah indikasi awal, bukan bukti pelanggaran. Keputusan ada pada Anda.")

        text = "\n".join(lines)
        try:
            await bot.send_message(
                chat_id=officer.telegram_chat_id,
                text=text,
                parse_mode=None,  # plain text to avoid Markdown parsing issues
            )
            sent += 1
            logger.info("notifier: morning message sent to officer %s", officer.officer_id)
        except Exception as exc:
            logger.error("notifier: failed to send to %s: %s", officer.officer_id, exc)

    return sent
