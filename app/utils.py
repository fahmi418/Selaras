from __future__ import annotations

from datetime import datetime, timezone

from app.config import RulesConfig, get_rules


def utcnow() -> datetime:
    """Naive UTC timestamp — semua kolom DateTime disimpan tanpa info zona."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def size_bucket_for(headcount: int, rules: RulesConfig | None = None) -> str:
    """Klasifikasi skala badan usaha; batas None berarti tak berbatas."""
    rules = rules or get_rules()
    caps = rules.size_bucket_caps
    for bucket in ("micro", "small", "medium"):
        cap = caps.get(bucket)
        if cap is None or headcount < cap:
            return bucket
    return "large"
