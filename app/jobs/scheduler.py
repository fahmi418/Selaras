"""
APScheduler setup — all background jobs defined here.

Jobs run in their own async session (not sharing request sessions).
Schedule times are in Asia/Jakarta (WIB).
"""

from __future__ import annotations

import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def _job_features() -> None:
    async with AsyncSessionLocal() as session:
        from app.risk.features import build_features
        n = await build_features(session, as_of=date.today())
        await session.commit()
        logger.info("scheduler: features built for %d companies", n)


async def _job_scoring() -> None:
    async with AsyncSessionLocal() as session:
        from app.risk.scoring import run_scoring
        n = await run_scoring(session, as_of=date.today())
        await session.commit()
        logger.info("scheduler: scored %d companies", n)


async def _job_queue() -> None:
    async with AsyncSessionLocal() as session:
        from app.queue.planner import build_daily_queue
        assignments = await build_daily_queue(session, target_date=date.today())
        await session.commit()
        logger.info("scheduler: queue built for %d officers", len(assignments))


async def _job_morning_message() -> None:
    async with AsyncSessionLocal() as session:
        from app.queue.notifier import send_morning_messages
        sent = await send_morning_messages(session, target_date=date.today())
        logger.info("scheduler: morning messages sent: %d", sent)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Jakarta")

    # Daily pipeline
    scheduler.add_job(_job_features, CronTrigger(hour=5, minute=0), id="features")
    scheduler.add_job(_job_scoring, CronTrigger(hour=5, minute=30), id="scoring")
    scheduler.add_job(_job_queue, CronTrigger(hour=5, minute=45), id="queue")
    scheduler.add_job(_job_morning_message, CronTrigger(hour=7, minute=30), id="morning_message")

    return scheduler
