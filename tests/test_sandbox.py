"""Uji sandbox fraud sintetis: seed deterministik, pola divergen, ringkasan."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.config import get_rules
from synth.fraud_sandbox import dataset_summary, generate_dataset


@pytest.fixture()
def rules():
    return deepcopy(get_rules())


def test_dataset_is_deterministic(rules):
    first = generate_dataset(rules)
    second = generate_dataset(rules)
    assert first == second


def test_dataset_covers_all_patterns_and_size(rules):
    dataset = generate_dataset(rules)
    expected_names = {
        "seasonal_mutation", "under_reporting_ramp", "micro_split", "clean_baseline",
    }
    assert set(dataset) == expected_names
    size = int(rules.sandbox["dataset_size"])
    for rows in dataset.values():
        assert len(rows) == size


def test_patterns_diverge_from_baseline(rules):
    dataset = generate_dataset(rules)
    clean = dataset["clean_baseline"]
    ramp = dataset["under_reporting_ramp"]
    assert any(row["remit_ratio"] < 1.0 for row in ramp)
    assert all(row["remit_ratio"] == 1.0 for row in clean)

    mutation = dataset["seasonal_mutation"]
    assert any(row["headcount_gap"] > 0 for row in mutation)
    assert all(row["headcount_gap"] == 0 for row in clean)

    split = dataset["micro_split"]
    assert any(row["est_headcount"] > row["registered_headcount"] for row in split)


def test_rows_carry_complete_feature_columns(rules):
    dataset = generate_dataset(rules)
    required = {
        "under_reporting_ratio", "remit_ratio", "headcount_gap", "churn_rate",
        "registered_headcount", "est_headcount", "median_wage", "umk",
    }
    for rows in dataset.values():
        for row in rows[:3]:
            assert required.issubset(row)


def test_summary_reports_each_pattern_with_stress_stats(rules):
    summary = dataset_summary(rules)
    names = {item["pattern"] for item in summary}
    assert names == {"seasonal_mutation", "under_reporting_ramp", "micro_split", "clean_baseline"}
    for item in summary:
        assert item["rows"] == int(rules.sandbox["dataset_size"])
        assert item["under_reporting_ratio"] is not None
        assert item["headcount_gap"] is not None
