"""Tests helper utilitas: waktu UTC naive & klasifikasi skala perusahaan."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.config import get_rules
from app.utils import size_bucket_for, utcnow


def test_utcnow_is_naive_utc():
    aware = datetime.now(timezone.utc)
    value = utcnow()

    assert value.tzinfo is None
    delta = abs((aware - value.replace(tzinfo=timezone.utc)).total_seconds())
    assert delta < 5


def test_size_bucket_boundaries_from_rules():
    rules = get_rules()
    caps = rules.size_bucket_caps
    # Default rules: micro <10, small <50, medium <200, sisanya large.
    expected = {
        1: "micro",
        9: "micro",
        10: "small",
        49: "small",
        50: "medium",
        199: "medium",
        200: "large",
        10_000: "large",
    }
    for headcount, bucket in expected.items():
        assert size_bucket_for(headcount, rules) == bucket, headcount


def test_size_bucket_custom_rules():
    rules = get_rules()
    base = deepcopy(rules.company["size_buckets"])
    rules.company["size_buckets"] = {"micro": 5, "small": 20, "medium": None}
    try:
        assert size_bucket_for(4, rules) == "micro"
        assert size_bucket_for(5, rules) == "small"
        assert size_bucket_for(19, rules) == "small"
        # medium tanpa batas -> semua >= small jatuh ke medium, bukan large.
        assert size_bucket_for(500, rules) == "medium"
    finally:
        rules.company["size_buckets"] = base
