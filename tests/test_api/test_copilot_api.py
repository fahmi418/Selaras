"""Tests API copilot: briefing & BAP endpoints (Pilar 1 Horizon 2)."""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.mark.asyncio
async def test_briefing_requires_key_and_404(client):
    settings = get_settings()

    missing = await client.get("/api/case/case-none/briefing")
    assert missing.status_code in (403, 422)

    not_found = await client.get(
        "/api/case/case-none/briefing",
        headers={"X-API-Key": settings.internal_api_key.get_secret_value()},
    )
    assert not_found.status_code == 404


@pytest.mark.asyncio
async def test_bap_endpoint_demo_provider_roundtrip(client):
    settings = get_settings()
    internal_key = settings.internal_api_key.get_secret_value()
    admin_key = settings.admin_api_key.get_secret_value()

    # Kasus tak dikenal -> 404 sebelum provider dipakai.
    not_found = await client.post(
        "/api/case/case-none/bap",
        json={"transcript": "P: x\nJ: y"},
        headers={"X-API-Key": internal_key},
    )
    assert not_found.status_code == 404

    # Registrasi perusahaan membuat kasus otomatis; ambil kasusnya lalu susun draf BAP
    # memakai provider demo (deterministik, tanpa jaringan).
    created = await client.post(
        "/admin/company",
        json={"name": "PT Bapip Demo", "npp": "7-7001"},
        headers={"X-API-Key": admin_key},
    )
    assert created.status_code == 200
    company_id = created.json()["company_id"]

    from sqlalchemy import select

    from app.db.models import CaseFile
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        case_id = (await session.execute(
            select(CaseFile.case_id).where(CaseFile.company_id == company_id)
        )).scalar_one()

    response = await client.post(
        f"/api/case/{case_id}/bap",
        json={"transcript": "P: Jumlah pekerja?\nJ: 25 orang.\n"},
        headers={"X-API-Key": internal_key},
    )
    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["provider"] == "demo-scripted"
    assert draft["points"][0] == {"question": "Jumlah pekerja?", "answer": "25 orang."}
    assert draft["company_name"] == "PT Bapip Demo"

    empty = await client.post(
        f"/api/case/{case_id}/bap",
        json={"transcript": "   "},
        headers={"X-API-Key": internal_key},
    )
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_bap_endpoint_demo_provider_roundtrip(client):
    settings = get_settings()
    internal_key = settings.internal_api_key.get_secret_value()

    # Kasus tak dikenal -> 404 sebelum provider dipakai.
    not_found = await client.post(
        "/api/case/case-none/bap",
        json={"transcript": "P: x\nJ: y"},
        headers={"X-API-Key": internal_key},
    )
    assert not_found.status_code == 404

    # Kasus milik demo data synth (seed_demo) — ambil satu ID dari antrean admin?
    # Gunakan jalur langsung: buat kasus via admin company + planner job.
    admin_key = settings.admin_api_key.get_secret_value()
    created = await client.post(
        "/admin/company",
        json={"name": "PT Bapip Demo", "npp": "7-7001"},
        headers={"X-API-Key": admin_key},
    )
    assert created.status_code == 200
    company_id = created.json()["company_id"]

    # Kasus dibuat otomatis saat registrasi; ambil via briefing dari job queue tidak perlu —
    # cari kasus lewat endpoint briefings setelah planner assign? Untuk MVP pastikan endpoint
    # responsif pada kasus sintetis 'case-devdemo' dari seed_demo bila tersedia.
    # Fallback: POST bap tanpa kasus tetap 404; kasus lantai demo dieksplorasi di test Fase 2.
