"""Bot conversation states and InlineKeyboard builders."""

from __future__ import annotations

from enum import IntEnum, auto


class ConvState(IntEnum):
    IDLE = auto()
    AWAITING_CONSENT = auto()
    SELECTING_COMPANY = auto()
    AWAITING_PHOTO = auto()
    AWAITING_CLARIFICATION = auto()   # ambiguous component question
    AWAITING_ACTION = auto()          # after verdict: report / ignore / explain
    AWAITING_STATUS_Q = auto()        # P1: work status question
    AWAITING_START_DATE_Q = auto()    # P1: start date question
