"""
Append-only audit log writer.

All significant system actions are recorded here.
Records are never modified or deleted from application code.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

logger = logging.getLogger(__name__)


async def write_audit(
    session: AsyncSession,
    actor: str,
    action: str,
    object_ref: str,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """
    Append an audit log entry.

    actor:      worker_hash | officer_id | "system" | "admin"
    action:     SLIP_CHECK | REPORT | CASE_ACTION | VISIT_OUTCOME | DATA_RESET | JOB_RUN | ...
    object_ref: company_id | case_id | "all" | ...
    meta:       additional context (verdict codes, result, etc.)
    """
    entry = AuditLog(
        actor=actor,
        action=action,
        object=object_ref,
        meta_json=json.dumps(meta) if meta else None,
    )
    session.add(entry)
    # Note: caller is responsible for commit; audit is part of the same transaction.
