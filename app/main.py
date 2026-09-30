"""
FastAPI application factory with lifespan management.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Initialize DB
    from app.db.session import create_tables
    await create_tables()
    logger.info("database tables verified/created")

    # Auto-seed demo data if DB is empty and demo_mode is enabled
    if settings.demo_mode:
        try:
            from sqlalchemy import select, func
            from app.db.models import Company
            from app.db.session import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                count_res = await session.execute(select(func.count()).select_from(Company))
                count = count_res.scalar_one_or_none() or 0
                if count == 0:
                    logger.info("demo mode active and database empty, seeding initial demo data")
                    from synth.seed_demo import seed
                    await seed()
                    logger.info("demo data seeded successfully")
        except Exception as exc:
            logger.warning("auto-seeding failed or skipped", error=str(exc))

    # Build and initialize bot application
    bot_app = None
    try:
        from app.bot.gateway import build_application
        bot_app = await build_application()
        await bot_app.initialize()

        # Resolve public HTTPS base URL for Telegram Webhook
        import os
        domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("RAILWAY_STATIC_URL")
        if domain:
            base_url = f"https://{domain.replace('https://', '').replace('http://', '').strip('/')}"
        elif settings.app_base_url.startswith("https://"):
            base_url = settings.app_base_url.rstrip("/")
        elif "railway.app" in settings.app_base_url:
            base_url = settings.app_base_url.replace("http://", "https://").rstrip("/")
        elif settings.demo_mode and "localhost" in settings.app_base_url:
            base_url = "https://selaras-production.up.railway.app"
        else:
            base_url = settings.app_base_url.rstrip("/")

        if base_url.startswith("https://"):
            webhook_url = f"{base_url}/webhook/telegram"
            await bot_app.bot.set_webhook(
                url=webhook_url,
                secret_token=settings.telegram_webhook_secret.get_secret_value(),
            )
            logger.info("telegram webhook set successfully", url=webhook_url)
        else:
            logger.warning("telegram webhook skipped: HTTPS URL is required by Telegram", base_url=base_url)
    except Exception as exc:
        logger.warning("telegram bot initialization skipped in demo mode", error=str(exc))

    # Start scheduler
    from app.jobs.scheduler import build_scheduler
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("scheduler started")

    yield

    # Cleanup
    scheduler.shutdown(wait=False)
    if bot_app:
        await bot_app.shutdown()
    logger.info("application shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Selaras API",
        description="Deteksi ketidakselarasan iuran JKN berbasis triangulasi",
        version="1.0.0",
        docs_url="/docs" if settings.demo_mode else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.demo_mode else [settings.app_base_url],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Webhook endpoint
    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request):
        secret = settings.telegram_webhook_secret.get_secret_value()
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_secret != secret:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        update_json = await request.json()
        from app.bot.gateway import process_update
        await process_update(update_json)
        return {"ok": True}

    # Mount routers
    from app.web.routes import router as web_router
    from app.api.internal import router as api_router

    app.include_router(web_router)
    app.include_router(api_router)

    # Static files
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

    return app


app = create_app()
