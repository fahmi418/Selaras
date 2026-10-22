"""
Feature Attribution — Horizon 2 Pilar 3.

Menyajikan kontribusi sinyal (bobot relatif) per perusahaan agar penetapan
antrean audit bisa diaudit keadilannya (tanpa bias sektor/geografis yang
disembunyikan di dalam kotak hitam).
"""

from __future__ import annotations

from typing import Any

from app.cases.service import SIGNAL_LABELS


def signal_attribution(signals: dict[str, Any]) -> list[dict[str, Any]]:
    """Kontribusi sinyal diurutkan menurun beserta porsi relatif (0-1)."""
    contributions = {
        key: float(value or 0.0) for key, value in signals.items() if float(value or 0.0) > 0.0
    }
    total = sum(contributions.values())
    if total <= 0.0:
        return []

    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    attribution = []
    for key, contribution in ranked:
        label, source = SIGNAL_LABELS.get(key, (key, key))
        attribution.append(
            {
                "signal": key,
                "label": label,
                "source": source,
                "contribution": round(contribution, 4),
                "share": round(contribution / total, 4),
            }
        )
    return attribution
