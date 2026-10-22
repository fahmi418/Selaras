"""
What-If Simulator deklarasi mandiri (Pilar 4 — Proactive Self-Correction).

Badan usaha mendeklarasikan perbaikan (pendaftaran pekerja baru, upah riil,
setoran lunas); simulator mengulang skor aturan pada vektor fitur hasil
perbaikan. Tidak ada data yang dimutasi — hasil murni indikatif.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.config import RulesConfig, get_rules
from app.db.models import CompanyFeatures
from app.risk.scoring import get_latest_score
from app.xai.attribution import signal_attribution
from app.xai.counterfactual import (
    _SCORE_COLUMNS,
    _risk_from_row,
    true_wage_ratio,
)


class SelfCorrectionDeclaration(BaseModel):
    declared_new_workers: int = Field(default=0, ge=0)
    declare_benchmark_wage: bool = False
    declare_remit_fixed: bool = False


def apply_declaration(
    features: dict[str, Any],
    declaration: SelfCorrectionDeclaration,
    rules: RulesConfig | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Salinan fitur hasil deklarasi + keterangan langkah yang diterapkan."""
    rules = rules or get_rules()
    row = dict(features)
    applied: list[str] = []

    add = declaration.declared_new_workers
    if add > 0:
        registered = max(0.0, float(row.get("registered_headcount", 0) or 0))
        max_share = float(rules.xai["what_if"]["max_new_workers_share"])
        cap = round(max_share * max(registered, 1.0))
        add = min(add, cap)
        row["registered_headcount"] = registered + add
        est = max(1.0, float(row.get("est_headcount", 1) or 1))
        row["headcount_gap"] = max(0.0, (est - row["registered_headcount"]) / est)
        applied.append(f"Pendaftaran {int(add)} pekerja baru (batas simulasi {int(cap)}).")

    if declaration.declare_benchmark_wage:
        row["avg_wage_ratio_to_umk"] = true_wage_ratio(rules)
        row["pct_wage_at_umk"] = 0.0
        applied.append("Upah riil disesuaikan dengan patokan industri (rules.xai).")

    if declaration.declare_remit_fixed:
        row["remit_ratio"] = 1.0
        row["unpaid_months"] = 0.0
        applied.append("Seluruh tunggakan iuran dinyatakan lunas.")

    return row, applied


async def _latest_features(
    session: Any, company_id: str
) -> Optional[dict[str, Any]]:
    result = await session.execute(
        select(CompanyFeatures)
        .where(CompanyFeatures.company_id == company_id)
        .order_by(desc(CompanyFeatures.as_of))
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None or not snapshot.feature_json:
        return None
    return json.loads(snapshot.feature_json)


async def what_if_for_company(
    session: Any,
    company_id: str,
    declaration: SelfCorrectionDeclaration,
    rules: RulesConfig | None = None,
) -> Optional[dict]:
    """Risiko sebelum vs sesudah deklarasi mandiri, lengkap dengan asumsinya."""
    rules = rules or get_rules()
    features = await _latest_features(session, company_id)
    if features is None:
        return None

    score = await get_latest_score(session, company_id)
    anomaly = float(score.anomaly_score) if score is not None else 0.0

    risk_before = _risk_from_row(features, anomaly, rules)
    row_after, applied = apply_declaration(features, declaration, rules)
    risk_after = _risk_from_row(row_after, anomaly, rules)

    signals_after = {
        key: row_after.get(key, 0.0) for key in _SCORE_COLUMNS if key not in ("headcount_gap",)
    }
    return {
        "company_id": company_id,
        "rule_version": rules.version,
        "risk_before": round(risk_before, 1),
        "risk_after": round(risk_after, 1),
        "risk_delta": round(risk_after - risk_before, 1),
        "applied": applied,
        "attribution_after": signal_attribution(signals_after),
        "assumptions": [
            "Skor aturan dihitung ulang sepenuhnya (deterministik).",
            "Skor anomali IsolationForest dipegang tetap dari snapshot sebelumnya.",
            "Hasil murni indikatif; verifikasi dilakukan petugas Wasrik.",
        ],
    }
