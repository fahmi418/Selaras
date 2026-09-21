"""InlineKeyboard builders for all bot flows."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Setuju & Lanjut", callback_data="consent:yes"),
            InlineKeyboardButton("Baca Kebijakan Privasi", callback_data="consent:read"),
        ]
    ])


def decline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Tidak Setuju", callback_data="consent:no")]
    ])


def verdict_action_keyboard(check_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Laporkan (Anonim)", callback_data=f"action:report:{check_id}"),
            InlineKeyboardButton("Abaikan", callback_data=f"action:ignore:{check_id}"),
        ],
        [InlineKeyboardButton("Penjelasan Detail", callback_data=f"action:explain:{check_id}")],
    ])


def clarification_keyboard(check_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Ya, tetap", callback_data=f"clarify:fixed:{check_id}"),
            InlineKeyboardButton("Tidak tetap", callback_data=f"clarify:variable:{check_id}"),
            InlineKeyboardButton("Tidak tahu", callback_data=f"clarify:unknown:{check_id}"),
        ]
    ])


def work_status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Karyawan tetap", callback_data="status:karyawan_tetap"),
            InlineKeyboardButton("Kontrak", callback_data="status:kontrak"),
        ],
        [
            InlineKeyboardButton("Harian", callback_data="status:harian"),
            InlineKeyboardButton("Mitra/freelance", callback_data="status:mitra"),
        ],
        [InlineKeyboardButton("Tidak tahu", callback_data="status:tidak_tahu")],
    ])


def skip_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Lewati →", callback_data=f"skip:{action}")]
    ])
