"""Tests konfigurasi: validasi fail-fast RulesConfig dan accessor turunan."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from app.config import RulesConfig, get_rules


def test_get_rules_loads_all_required_sections():
    rules = get_rules()
    assert rules.version
    for attr in (
        "contribution", "tolerance", "extraction", "escalation", "scoring",
        "estimator", "planner", "cases", "thresholds", "features", "company",
        "rate_limit", "retention", "morning_message", "risk_labels",
    ):
        assert getattr(rules, attr), attr


def _rules_dict() -> dict:
    """Bentuk raw dict dari rules.yaml aktif untuk membangun RulesConfig pembanding."""
    from app.config import _REQUIRED_RULE_KEYS, get_rules

    rules = get_rules()
    raw = {
        "rule_version": rules.version,
        "contribution": {
            "total_rate": rules.contribution.total_rate,
            "employer_rate": rules.contribution.employer_rate,
            "worker_rate": rules.contribution.worker_rate,
            "wage_cap": rules.contribution.wage_cap,
            "wage_floor": rules.contribution.wage_floor,
        },
        "tolerance": {
            "absolute_idr": rules.tolerance.absolute_idr,
            "relative": rules.tolerance.relative,
        },
    }
    for key in _REQUIRED_RULE_KEYS:
        if key not in raw:
            raw[key] = getattr(rules, key)
    return raw


def test_missing_section_raises_runtime_error(tmp_path):
    raw = _rules_dict()
    raw.pop("cases")  # bagian wajib dihilangkan -> harus gagal cepat

    path = tmp_path / "broken-rules.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cases"):
        RulesConfig(path)


def test_missing_rules_file_raises(tmp_path):
    with pytest.raises((RuntimeError, OSError)):
        RulesConfig(tmp_path / "does-not-exist.yaml")


def test_derived_accessors():
    rules = get_rules()
    assert rules.default_umk == rules.thresholds["default_umk"]
    assert rules.min_independent_reports == rules.escalation["min_independent_reports"]
    assert rules.require_data_anomaly is bool(rules.escalation["require_data_anomaly"])
    assert rules.data_only_risk_threshold == rules.escalation["data_only_risk_threshold"]
    assert rules.size_bucket_caps["micro"] == rules.company["size_buckets"]["micro"]
