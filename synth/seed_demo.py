"""
Demo seeder for Selaras frontend & officer portal.
Populates sqlite database with realistic entities, officers, risk scores, and case files.
"""

import asyncio
import json
import secrets
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from app.db.models import (
    Base,
    Billing,
    CaseFile,
    Company,
    Enrollment,
    Officer,
    Region,
    RiskScore,
    SectorBenchmark,
    VisitOutcome,
    WorkerReport,
)
from app.db.session import AsyncSessionLocal, create_tables

_REGIONS = [
    {"region_id": "jkt-utara", "province": "DKI Jakarta", "city": "Jakarta Utara", "umk": 5_450_000},
    {"region_id": "jkt-selatan", "province": "DKI Jakarta", "city": "Jakarta Selatan", "umk": 5_450_000},
    {"region_id": "kab-bekasi", "province": "Jawa Barat", "city": "Kab. Bekasi", "umk": 5_380_000},
    {"region_id": "kota-surabaya", "province": "Jawa Timur", "city": "Kota Surabaya", "umk": 4_725_479},
]

_OFFICERS = [
    {"officer_id": "officer-01", "name": "Ahmad Fauzi", "region_id": "jkt-utara", "daily_hours": 8.0, "telegram_chat_id": "111222"},
    {"officer_id": "officer-02", "name": "Siti Nurhaliza", "region_id": "kab-bekasi", "daily_hours": 7.5, "telegram_chat_id": "222333"},
    {"officer_id": "officer-03", "name": "Budi Santoso", "region_id": "kota-surabaya", "daily_hours": 8.0, "telegram_chat_id": "333444"},
    {"officer_id": "officer-04", "name": "Dewi Lestari", "region_id": "jkt-selatan", "daily_hours": 8.0, "telegram_chat_id": "444555"},
]

_COMPANIES = [
    {
        "name": "PT Cipta Logistik Nusantara",
        "npp": "NPP-0029381",
        "sector": "logistik",
        "region_id": "jkt-utara",
        "size_bucket": "medium",
        "est_headcount": 180,
        "registered_headcount": 115,
        "risk": 86.4,
        "risk_label": "Tinggi",
        "signals": {"C1_under_reporting": 0.78, "C2_headcount_gap": 0.65, "C3_remit_inconsistent": 0.40, "C6_worker_reports": 0.85},
        "est_low": 24_000_000,
        "est_mid": 38_500_000,
        "est_high": 52_000_000,
        "p_valid": 0.89,
        "assigned_to": "officer-01",
        "reasons": [
            {"title": "Under-reporting Upah Riil (Modus #2)", "evidence": "Upah terdaftar Rp3.800.000 vs indikasi slip Rp5.600.000 (selisih 32%).", "source": "Triangulasi Slip & BPJS"},
            {"title": "Pendaftaran Pekerja Sebagian (Modus #1)", "evidence": "65 pekerja aktif tidak terdaftar pada basis data kepesertaan.", "source": "Data Presensi & BPJS"},
            {"title": "Laporan Mandiri Pekerja Terverifikasi", "evidence": "4 pekerja independen melaporkan potongan 1% tidak sesuai slip gaji.", "source": "Laporan Pekerja"}
        ]
    },
    {
        "name": "PT Tekstil Makmur Sejahtera",
        "npp": "NPP-0048192",
        "sector": "manufaktur",
        "region_id": "jkt-utara",
        "size_bucket": "large",
        "est_headcount": 340,
        "registered_headcount": 320,
        "risk": 74.2,
        "risk_label": "Tinggi",
        "signals": {"C1_under_reporting": 0.82, "C3_remit_inconsistent": 0.55},
        "est_low": 32_000_000,
        "est_mid": 45_000_000,
        "est_high": 68_000_000,
        "p_valid": 0.82,
        "assigned_to": "officer-01",
        "reasons": [
            {"title": "Penggelapan Iuran (Modus #3)", "evidence": "Potongan iuran 1% tertera pada slip, namun setoran 3 bulan terakhir nihil.", "source": "Billing BPJS & Slip"},
            {"title": "Penyeragaman Upah ke UMK", "evidence": "98% pekerja dari staf hingga teknisi didaftarkan tepat pada angka UMK.", "source": "Data Kepesertaan"}
        ]
    },
    {
        "name": "PT Sentosa Pangan Sejahtera",
        "npp": "NPP-0083719",
        "sector": "fb",
        "region_id": "jkt-utara",
        "size_bucket": "small",
        "est_headcount": 45,
        "registered_headcount": 38,
        "risk": 58.6,
        "risk_label": "Sedang",
        "signals": {"C1_under_reporting": 0.45, "C4_uniform_wage": 0.50},
        "est_low": 6_000_000,
        "est_mid": 9_800_000,
        "est_high": 14_500_000,
        "p_valid": 0.71,
        "assigned_to": "officer-01",
        "reasons": [
            {"title": "Indikasi Penyeragaman Upah (C4)", "evidence": "Seluruh staf dapur dan kasir dilaporkan seragam pada batas bawah upah minimum.", "source": "Data Kepesertaan"},
            {"title": "Diskrepansi Tunjangan Tetap", "evidence": "Tunjangan operasional tidak diikutsertakan dalam dasar perhitungan 1% iuran.", "source": "Slip Gaji"}
        ]
    },
    {
        "name": "PT Jaya Teknik Mandiri",
        "npp": "NPP-0091823",
        "sector": "konstruksi",
        "region_id": "jkt-utara",
        "size_bucket": "small",
        "est_headcount": 60,
        "registered_headcount": 55,
        "risk": 32.0,
        "risk_label": "Rendah",
        "signals": {"C5_churn": 0.20},
        "est_low": 2_000_000,
        "est_mid": 3_500_000,
        "est_high": 5_000_000,
        "p_valid": 0.60,
        "assigned_to": "officer-01",
        "reasons": [
            {"title": "Fluktuasi Kepesertaan Musiman", "evidence": "Penurunan kepesertaan terkait selesainya proyek konstruksi tahap 1.", "source": "Data Mutasi"}
        ]
    },
    {
        "name": "PT Mega Niaga Bekasi",
        "npp": "NPP-0056123",
        "sector": "ritel",
        "region_id": "kab-bekasi",
        "size_bucket": "medium",
        "est_headcount": 120,
        "registered_headcount": 80,
        "risk": 79.5,
        "risk_label": "Tinggi",
        "signals": {"C2_headcount_gap": 0.75, "C4_uniform_wage": 0.60},
        "est_low": 18_000_000,
        "est_mid": 28_000_000,
        "est_high": 40_000_000,
        "p_valid": 0.85,
        "assigned_to": "officer-02",
        "reasons": [
            {"title": "Pendaftaran Sebagian Pekerja Toko", "evidence": "40 staf operasional tidak didaftarkan ke program JKN.", "source": "Laporan Pekerja & Presensi"}
        ]
    },
    {
        "name": "PT Surabaya Presisi Otomotif",
        "npp": "NPP-0077221",
        "sector": "manufaktur",
        "region_id": "kota-surabaya",
        "size_bucket": "large",
        "est_headcount": 290,
        "registered_headcount": 210,
        "risk": 82.1,
        "risk_label": "Tinggi",
        "signals": {"C1_under_reporting": 0.85, "C2_headcount_gap": 0.60},
        "est_low": 26_000_000,
        "est_mid": 39_000_000,
        "est_high": 55_000_000,
        "p_valid": 0.88,
        "assigned_to": "officer-03",
        "reasons": [
            {"title": "Under-reporting Upah Operator Pabrik", "evidence": "Rata-rata dilaporkan Rp3.500.000 padahal gaji riil Rp5.200.000.", "source": "Slip Gaji & BPJS"}
        ]
    }
]

_CHECKLIST_TEMPLATE = [
    {"item": "Daftar penggajian (payroll) internal lengkap periode 3 bulan terakhir", "required": True},
    {"item": "Bukti potong pajak formulir 1721-A1 atau bukti transfer bank slip gaji", "required": True},
    {"item": "Daftar hadir / absensi fisik tenaga kerja aktif di lokasi kerja", "required": True},
    {"item": "Surat Perjanjian Kerja (PKWT/PKWTT) untuk konfirmasi status karyawan vs mitra", "required": False},
    {"item": "Rekening koran perusahaan bukti penyetoran iuran ke Virtual Account BPJS", "required": True},
    {"item": "Konfirmasi perwakilan serikat pekerja / perwakilan karyawan secara tertutup", "required": False},
]

async def seed():
    from sqlalchemy import text
    await create_tables()

    print("Cleaning database tables for a fresh demo state...")
    async with AsyncSessionLocal() as session:
        for tbl in [
            "visit_outcome", "case_file", "risk_score", "worker_report",
            "slip_check", "worker_consent", "billing", "enrollment",
            "company", "officer", "sector_benchmark", "region",
        ]:
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()

    async with AsyncSessionLocal() as session:
        # 1. Regions
        for r in _REGIONS:
            session.add(Region(
                region_id=r["region_id"],
                province=r["province"],
                city=r["city"],
                umk=r["umk"],
                umk_year=2026,
            ))
            # Sector benchmarks
            for sec in ["manufaktur", "ritel", "fb", "jasa", "konstruksi", "logistik"]:
                session.add(SectorBenchmark(
                    sector=sec,
                    region_id=r["region_id"],
                    median_wage=int(r["umk"] * 1.15),
                    p25_wage=int(r["umk"] * 0.90),
                    p75_wage=int(r["umk"] * 1.40),
                ))

        # 2. Officers
        for off in _OFFICERS:
            session.add(Officer(**off))

        await session.flush()

        # 3. Companies, Risk Scores, and Cases
        for c_data in _COMPANIES:
            cid = str(uuid4())
            company = Company(
                company_id=cid,
                npp=c_data["npp"],
                name=c_data["name"],
                sector=c_data["sector"],
                region_id=c_data["region_id"],
                size_bucket=c_data["size_bucket"],
                est_headcount=c_data["est_headcount"],
                registered_headcount=c_data["registered_headcount"],
                founded_at=date(2018, 5, 12),
                status="active",
            )
            session.add(company)

            # Add Enrollments
            for i in range(min(15, c_data["registered_headcount"])):
                session.add(Enrollment(
                    enrollment_id=str(uuid4()),
                    company_id=cid,
                    worker_pid=f"pid-{secrets.token_hex(4)}",
                    reported_wage_base=int(4_200_000),
                    since=date(2023, 1, 1),
                    registered_status="active",
                ))

            # Add RiskScore
            session.add(RiskScore(
                company_id=cid,
                as_of=date.today(),
                rule_score=c_data["risk"] / 100,
                anomaly_score=c_data["risk"] / 100 * 0.9,
                risk=c_data["risk"],
                signals_json=json.dumps(c_data["signals"]),
                est_low=c_data["est_low"],
                est_mid=c_data["est_mid"],
                est_high=c_data["est_high"],
                p_valid=c_data["p_valid"],
                model_version="rule+isoforest-v1",
            ))

            # Add CaseFile with token
            token_plain = secrets.token_urlsafe(16)
            token_hash = sha256(token_plain.encode()).hexdigest()
            case_id = str(uuid4())

            session.add(CaseFile(
                case_id=case_id,
                company_id=cid,
                status="assigned",
                priority=c_data["risk"] * 100,
                assigned_officer=c_data["assigned_to"],
                created_at=datetime.utcnow(),
                due_date=date.today() + timedelta(days=2),
                reasons_json=json.dumps(c_data["reasons"]),
                checklist_json=json.dumps(_CHECKLIST_TEMPLATE),
                access_token_hash=token_hash,
                token_expires=datetime.utcnow() + timedelta(hours=24),
            ))

            # Add past visit outcome for history
            session.add(VisitOutcome(
                outcome_id=str(uuid4()),
                case_id=case_id,
                result="TERBUKTI",
                confirmed_modus="2",
                found_amount=12_000_000,
                note="Kunjungan pemeriksaan tahun lalu menemukan selisih upah lembur tidak diikutsertakan.",
                created_at=datetime.utcnow() - timedelta(days=180),
            ))

        await session.commit()
        print("Demo database seeded successfully.")

if __name__ == "__main__":
    asyncio.run(seed())
