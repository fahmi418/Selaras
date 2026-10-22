"""
Sandbox fraud sintetis (Horizon 3 Pilar 5).

Generator pola fraud deterministic (seeded) untuk mengevaluasi rule engine
tanpa data produksi: seasonal mutation, under-reporting ramp, micro split,
dan baseline patuh sebagai kontrol. Murni menghasilkan vektor fitur —
tidak menulis DB, tidak memanggil LLM.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from app.config import RulesConfig, get_rules

_PATTERN_NAMES = ("seasonal_mutation", "under_reporting_ramp", "micro_split", "clean_baseline")
_STRESS_KEYS = ("under_reporting_ratio", "remit_ratio", "headcount_gap", "churn_rate")


def _seed_for(company_name: str, rules: RulesConfig) -> int:
    material = f"{company_name}:{rules.sandbox['dataset_size']}"
    return int(hashlib.sha256(material.encode()).hexdigest()[:8], 16)


def _base_row(prng: random.Random, comp: dict[str, Any]) -> dict[str, float]:
    """Modal fitur 'patuh penuh' dengan deraup kecil, sebelum pola diterapkan."""
    est_headcount = float(comp.get("est_headcount", 50))
    umk = float(comp.get("umk", 3_000_000))
    median_wage = float(comp.get("median_wage") or umk * prng.uniform(1.0, 1.15))
    return {
        # kolom rule engine (rakan scoring._compute_rule_scores)
        "under_reporting_ratio": 1.0,
        "unpaid_months": 0.0,
        "worker_report_share": 0.0,
        "headcount_gap": 0.0,
        "remit_ratio": 1.0,
        "avg_wage_ratio_to_umk": round(median_wage / umk, 4),
        "pct_wage_at_umk": round(0.5 * prng.uniform(0.8, 1.2), 4),
        "churn_rate": float(comp.get("churn_rate", 0.05)),
        # kolom _FEATURE_COLUMNS (utk pipeline fitur lengkap)
        "wage_ratio_vs_sector": 1.0,
        "worker_report_count": 0.0,
        "unique_check_count": 0.0,
        "count_w1": 0.0,
        "count_w3": 0.0,
        "count_w4": 0.0,
        "company_age_months": float(prng.uniform(6, 84)),
        "size_bucket_encoded": 1.0,
        "umk": umk,
        "registered_headcount": est_headcount,
        "est_headcount": est_headcount,
        "median_wage": median_wage,
    }


def seasonal_mutation_pattern(prng: random.Random, cfg: dict) -> dict[str, float]:
    """Mutasi menjelang periode auditan menaikkan churn aneh dan gap headcount."""
    ramp_weeks = int(cfg["ramp_weeks"])
    exit_share = float(cfg["exit_share"])
    return {
        "headcount_gap": round(exit_share, 3),
        "churn_rate": round(min(exit_share * ramp_weeks / 12, 0.9), 3),
        "under_reporting_ratio": 1.0,
    }


def under_reporting_ramp_pattern(prng: random.Random, cfg: dict) -> dict[str, float]:
    """Setoran makin meleset sepanjang periode ramp."""
    start = float(cfg["start_share"])
    end = float(cfg["end_share"])
    months = int(cfg["months"])
    onset = (start - end) / max(months, 1)
    return {
        "remit_ratio": round(end, 3),
        "unpaid_months": round(months * 0.5, 2),
        "under_reporting_ratio": round(1.0 - onset, 3),
    }


def micro_split_pattern(prng: random.Random, cfg: dict) -> dict[str, float]:
    """Entitas dipecah melewati ambang headcount kecil → gap terdaftar besar."""
    threshold = int(cfg["threshold_headcount"])
    splits = int(cfg["split_entities"])
    return {
        "headcount_gap": round(min(splits / max(threshold, 1), 2.0), 3),
        "registered_headcount": float(threshold),
        "est_headcount": float(threshold * (splits + 1)),
    }


def clean_baseline_pattern(prng: random.Random, cfg: dict) -> dict[str, float]:
    """Kontrol patuh; hanya deraup upah ±noise share."""
    noise = float(cfg["noise_wage"])
    return {
        "pct_wage_at_umk": round(max(0.05, 0.5 * (1 - noise)), 3),
        "under_reporting_ratio": 1.0,
    }


_PATTERNS = {
    "seasonal_mutation": seasonal_mutation_pattern,
    "under_reporting_ramp": under_reporting_ramp_pattern,
    "micro_split": micro_split_pattern,
    "clean_baseline": clean_baseline_pattern,
}


def generate_dataset(rules: RulesConfig | None = None) -> dict[str, Any]:
    """
    Vektor fitur sintetis untuk seluruh pola di rules.yaml (jatah merata).

    Return {pattern_name: [row, ...]} dengan seluruh kolom _FEATURE_COLUMNS.
    """
    rules = rules or get_rules()
    size = int(rules.sandbox["dataset_size"])
    patterns: dict[str, Any] = rules.sandbox["patterns"]

    out: dict[str, list[dict[str, float]]] = {}
    for name in _PATTERN_NAMES:
        cfg = patterns.get(name)
        if cfg is None:
            continue
        prng_name = random.Random(_seed_for(name, rules))
        rows: list[dict[str, float]] = []
        for idx in range(size):
            comp = {
                "est_headcount": prng_name.uniform(8, 120),
                "umk": prng_name.uniform(2_500_000, 6_000_000),
                "median_wage": 0.0,
                "churn_rate": prng_name.uniform(0.02, 0.12),
            }
            comp["median_wage"] = comp["umk"] * prng_name.uniform(1.0, 1.25)
            row = _base_row(prng_name, comp)
            row.update(_PATTERNS[name](prng_name, cfg))
            row["company_label"] = f"{name}#{idx}"
            rows.append(row)
        out[name] = rows
    return out


def dataset_summary(rules: RulesConfig | None = None) -> list[dict[str, Any]]:
    """Ringkasan skor fitur kunci per pola (utk dashboard & uji rule engine)."""
    rules = rules or get_rules()
    data = generate_dataset(rules)
    summary: list[dict[str, Any]] = []
    for name, rows in data.items():
        aggregates: dict[str, float | None] = {}
        for key in _STRESS_KEYS:
            values = [float(row[key]) for row in rows if key in row]
            aggregates[key] = round(sum(values) / len(values), 4) if values else None
        summary.append(
            {
                "pattern": name,
                "rows": len(rows),
                **aggregates,
                "title": rules.sandbox["patterns"][name].get("title", name),
            }
        )
    return summary
