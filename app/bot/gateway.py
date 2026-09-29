"""
Bot gateway — registers handlers and configures the Application.
Called from main.py during lifespan startup.
"""

from __future__ import annotations

import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.bot.handlers.commands import (
    cmd_bantuan,
    cmd_hapus,
    cmd_privasi,
    cmd_start,
    cmd_status,
    handle_consent_callback,
)
from app.bot.handlers.slip_flow import handle_photo, handle_report_callback
from app.config import get_settings

logger = logging.getLogger(__name__)

_application: Application | None = None


def get_application() -> Application:
    global _application
    if _application is None:
        raise RuntimeError("Bot application not initialized. Call build_application() first.")
    return _application


def get_bot():
    global _application
    if _application is None:
        return None
    return _application.bot


async def build_application() -> Application:
    """Build and configure the python-telegram-bot Application."""
    global _application

    settings = get_settings()
    app = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cek", handle_photo))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("hapus", cmd_hapus))
    app.add_handler(CommandHandler("privasi", cmd_privasi))
    app.add_handler(CommandHandler("bantuan", cmd_bantuan))

    # Photo messages
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(handle_consent_callback, pattern=r"^consent:"))
    app.add_handler(CallbackQueryHandler(handle_report_callback, pattern=r"^action:report:"))

    _application = app
    logger.info("Bot application configured successfully")
    return app


async def process_update(update_json: dict) -> None:
    """Process a single Telegram update from the webhook."""
    from telegram import Update

    app = get_application()
    update = Update.de_json(update_json, app.bot)
    await app.process_update(update)
