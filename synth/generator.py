"""
Synthetic data generator — PRD §18.

Generates a realistic but fully fictional dataset with known ground-truth labels
for precision/recall/lift evaluation. All names, NPPs, NIKs are invented.

Usage (CLI):
    python -m synth.generator --companies 2000 --seed 42
    python -m synth.generator --companies 300 --seed 42  # fast test run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional
from uuid import uuid4

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static reference data (synthetic)
# ---------------------------------------------------------------------------

_REGIONS = [
    {"region_id": "jkt-utara", "province": "DKI Jakarta", "city": "Jakarta Utara", "umk": 5_450_000},
    {"region_id": "jkt-selatan", "province": "DKI Jakarta", "city": "Jakarta Selatan", "umk": 5_450_000},
    {"region_id": "kab-bekasi", "province": "Jawa Barat", "city": "Kab. Bekasi", "umk": 5_380_000},
    {"region_id": "kota-bandung", "province": "Jawa Barat", "city": "Kota Bandung", "umk": 4_209_855},
    {"region_id": "kota-surabaya", "province": "Jawa Timur", "city": "Kota Surabaya", "umk": 4_725_479},
    {"region_id": "kota-semarang", "province": "Jawa Tengah", "city": "Kota Semarang", "umk": 3_454_827},
    {"region_id": "kota-medan", "province": "Sumatera Utara", "city": "Kota Medan", "umk": 3_605_272},
    {"region_id": "kota-denpasar", "province": "Bali", "city": "Kota Denpasar", "umk": 3_312_527},
]

_SECTORS = ["manufaktur", "ritel", "fb", "jasa", "konstruksi", "pendidikan", "logistik"]

_SECTOR_WAGE_MULTIPLIERS = {
    "manufaktur": 1.2,
    "ritel": 0.9,
    "fb": 0.85,
    "jasa": 1.1,
    "konstruksi": 1.15,
    "pendidikan": 1.05,
    "logistik": 1.0,
}

_SECTOR_BENCHMARKS = {
    (sector, region["region_id"]): {
        "median_wage": round(region["umk"] * _SECTOR_WAGE_MULTIPLIERS[sector]),
        "p25_wage": round(region["umk"] * _SECTOR_WAGE_MULTIPLIERS[sector] * 0.8),
        "p75_wage": round(region["umk"] * _SECTOR_WAGE_MULTIPLIERS[sector] * 1.3),
    }
    for region in _REGIONS
    for sector in _SECTORS
}

_COMPANY_PREFIXES = ["PT", "CV", "UD", "Koperasi"]
_COMPANY_NAMES = [
    "Maju Bersama", "Sinar Jaya", "Karya Utama", "Gemilang Abadi", "Sejahtera Mandiri",
    "Prima Logistik", "Anugerah Sentosa", "Barokah Jaya", "Cipta Karya", "Delta Nusantara",
    "Fajar Makmur", "Global Teknik", "Harapan Baru", "Indah Permai", "Jaya Sakti",
    "Karya Mandiri", "Lestari Jaya", "Mega Utama", "Nusantara Prima", "Omega Solusi",
]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

@dataclass
class SyntheticCompany:
    company_id: str
    npp: str
    name: str
    sector: str
    region_id: str
    umk: int
    size_bucket: str
    est_headcount: int
    registered_headcount: int
    founded_at: date
    modus_label: Optional[int]           # None = clean, 1-5 = fraud modus
    is_multi_label: bool = False
    workers: list[dict] = field(default_factory=list)
    billings: list[dict] = field(default_factory=list)


def generate_companies(
    n: int = 2000,
    seed: int = 42,
    fraud_prevalence: float = 0.12,
) -> tuple[list[SyntheticCompany], list[dict], list[dict]]:
    """
    Generate synthetic companies with injected fraud scenarios.

    Returns:
        (companies, regions, sector_benchmarks)
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    regions = _REGIONS.copy()
    benchmarks = [
        {"sector": s, "region_id": r, **v}
        for (s, r), v in _SECTOR_BENCHMARKS.items()
    ]

    companies: list[SyntheticCompany] = []
    used_npps: set[str] = set()

    n_fraud = round(n * fraud_prevalence)
    fraud_slots = list(range(n))
    rng.shuffle(fraud_slots)
    fraud_set = set(fraud_slots[:n_fraud])

    for i in range(n):
        region = rng.choice(regions)
        sector = rng.choice(_SECTORS)
        umk = region["umk"]

        # Log-normal headcount: most small, few large
        est_headcount = max(5, round(np_rng.lognormal(mean=2.5, sigma=1.2)))
        est_headcount = min(est_headcount, 500)

        if est_headcount < 10:
            size_bucket = "micro"
        elif est_headcount < 50:
            size_bucket = "small"
        elif est_headcount < 200:
            size_bucket = "medium"
        else:
            size_bucket = "large"

        npp = _gen_npp(rng, used_npps)
        used_npps.add(npp)

        prefix = rng.choice(_COMPANY_PREFIXES)
        name_word = rng.choice(_COMPANY_NAMES)
        seq = rng.randint(1, 99)
        name = f"{prefix} {name_word} {seq:02d}"

        founded_years_ago = rng.randint(1, 20)
        founded_at = date.today().replace(year=date.today().year - founded_years_ago)

        modus = None
        if i in fraud_set:
            modus = rng.randint(1, 5)

        median_wage = _SECTOR_BENCHMARKS.get((sector, region["region_id"]), {}).get(
            "median_wage", umk * 1.1
        )

        registered_headcount, workers, billings = _inject_scenario(
            modus=modus,
            est_headcount=est_headcount,
            region_id=region["region_id"],
            umk=umk,
            median_wage=int(median_wage),
            rng=rng,
            np_rng=np_rng,
        )

        companies.append(SyntheticCompany(
            company_id=str(uuid4()),
            npp=npp,
            name=name,
            sector=sector,
            region_id=region["region_id"],
            umk=umk,
            size_bucket=size_bucket,
            est_headcount=est_headcount,
            registered_headcount=registered_headcount,
            founded_at=founded_at,
            modus_label=modus,
            workers=workers,
            billings=billings,
        ))

    return companies, regions, benchmarks


def _gen_npp(rng: random.Random, used: set[str]) -> str:
    while True:
        npp = f"{rng.randint(100000, 999999)}-{rng.randint(100, 999)}"
        if npp not in used:
            return npp


def _inject_scenario(
    modus: Optional[int],
    est_headcount: int,
    region_id: str,
    umk: int,
    median_wage: int,
    rng: random.Random,
    np_rng: np.random.Generator,
) -> tuple[int, list[dict], list[dict]]:
    """
    Returns (registered_headcount, [worker_dicts], [billing_dicts]).
    Worker and billing data contain known ground-truth labels.
    """
    base_wage = max(umk, round(median_wage * np_rng.lognormal(0, 0.2)))
    base_wage = min(base_wage, 12_000_000)

    registered_headcount = est_headcount
    workers = []
    billings = []
    current_period = date.today().replace(day=1)

    # Generate 6 months of billing
    def _gen_billing(paid_frac: float = 1.0):
        total_wage = base_wage * registered_headcount
        billed = round(0.05 * total_wage)
        paid = round(billed * paid_frac)
        for m in range(6):
            period_date = current_period - timedelta(days=m * 30)
            period_str = period_date.strftime("%Y-%m")
            is_paid = rng.random() < paid_frac
            billings.append({
                "period": period_str,
                "billed_amount": billed,
                "paid_amount": paid if is_paid else 0,
                "paid_at": period_date.isoformat() if is_paid else None,
            })

    if modus is None:
        # Clean company: all registered, full payment
        _gen_billing(paid_frac=1.0)
        for j in range(est_headcount):
            workers.append({
                "worker_pid": f"W{j:04d}",
                "reported_wage_base": base_wage,
                "registered_status": "karyawan_tetap",
                "since": (current_period - timedelta(days=365)).isoformat(),
            })

    elif modus == 1:
        # Pendaftaran Pekerja Sebagian: only 40-80% registered
        ratio = rng.uniform(0.4, 0.8)
        registered_headcount = max(1, round(est_headcount * ratio))
        _gen_billing()
        for j in range(registered_headcount):
            workers.append({
                "worker_pid": f"W{j:04d}",
                "reported_wage_base": base_wage,
                "registered_status": "karyawan_tetap",
                "since": (current_period - timedelta(days=365)).isoformat(),
            })

    elif modus == 2:
        # Under-reporting: reported wage 50-80% of actual
        ratio = rng.uniform(0.5, 0.8)
        reported_wage = max(umk, round(base_wage * ratio))
        _gen_billing()
        for j in range(est_headcount):
            workers.append({
                "worker_pid": f"W{j:04d}",
                "reported_wage_base": reported_wage,  # under-reported
                "registered_status": "karyawan_tetap",
                "since": (current_period - timedelta(days=365)).isoformat(),
            })

    elif modus == 3:
        # Penggelapan Iuran: deducted from worker but not remitted 1-6 months
        unpaid_months = rng.randint(1, 6)
        for m in range(6):
            period_date = current_period - timedelta(days=m * 30)
            billed = round(0.05 * base_wage * est_headcount)
            paid = 0 if m < unpaid_months else billed
            billings.append({
                "period": period_date.strftime("%Y-%m"),
                "billed_amount": billed,
                "paid_amount": paid,
                "paid_at": period_date.isoformat() if paid else None,
            })
        for j in range(est_headcount):
            workers.append({
                "worker_pid": f"W{j:04d}",
                "reported_wage_base": base_wage,
                "registered_status": "karyawan_tetap",
                "since": (current_period - timedelta(days=365)).isoformat(),
            })

    elif modus == 4:
        # Misklasifikasi Status Kerja: 20-50% listed as "mitra"
        mitra_ratio = rng.uniform(0.2, 0.5)
        _gen_billing()
        for j in range(est_headcount):
            status = "mitra" if j < round(est_headcount * mitra_ratio) else "karyawan_tetap"
            workers.append({
                "worker_pid": f"W{j:04d}",
                "reported_wage_base": base_wage,
                "registered_status": status,
                "since": (current_period - timedelta(days=365)).isoformat(),
            })

    elif modus == 5:
        # Manipulasi Data Mutasi: since date delayed 2-6 months
        delay_months = rng.randint(2, 6)
        _gen_billing()
        actual_since = current_period - timedelta(days=365)
        delayed_since = actual_since + timedelta(days=delay_months * 30)
        for j in range(est_headcount):
            workers.append({
                "worker_pid": f"W{j:04d}",
                "reported_wage_base": base_wage,
                "registered_status": "karyawan_tetap",
                "since": delayed_since.isoformat(),  # delayed
            })

    return registered_headcount, workers, billings


async def generate_and_load(session, seed: int = 42, n_companies: int = 2000) -> int:
    """Generate synthetic data and bulk-insert into DB. Returns company count."""
    from sqlalchemy import text

    from app.db.models import (
        Billing, Company, Enrollment, Region, SectorBenchmark,
    )

    logger.info("synth: generating %d companies (seed=%d)…", n_companies, seed)
    companies, regions_data, benchmarks_data = generate_companies(n=n_companies, seed=seed)

    # Clear existing synthetic data
    for table in ["billing", "enrollment", "company", "sector_benchmark", "region"]:
        await session.execute(text(f"DELETE FROM {table}"))

    # Insert regions
    for r in regions_data:
        session.add(Region(umk_year=2026, **r))

    # Insert sector benchmarks
    for b in benchmarks_data:
        session.add(SectorBenchmark(**b))

    await session.flush()

    # Insert companies + workers + billings
    for c in companies:
        session.add(Company(
            company_id=c.company_id,
            npp=c.npp,
            name=c.name,
            sector=c.sector,
            region_id=c.region_id,
            size_bucket=c.size_bucket,
            est_headcount=c.est_headcount,
            registered_headcount=c.registered_headcount,
            founded_at=c.founded_at,
            status="active",
        ))

        for w in c.workers:
            session.add(Enrollment(
                enrollment_id=str(uuid4()),
                company_id=c.company_id,
                **w,
            ))

        for b in c.billings:
            session.add(Billing(company_id=c.company_id, **b))

    await session.flush()
    logger.info("synth: %d companies loaded", len(companies))
    return len(companies)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Selaras synthetic data generator")
    parser.add_argument("--companies", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    async def main():
        from app.db.session import AsyncSessionLocal, create_tables
        await create_tables()
        async with AsyncSessionLocal() as session:
            n = await generate_and_load(session, seed=args.seed, n_companies=args.companies)
            await session.commit()
            print(f"Generated and loaded {n} companies.")

    asyncio.run(main())
