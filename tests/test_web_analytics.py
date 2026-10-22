"""Uji halaman web analitik Fase 3: forecast, crossdoc (GET/POST), sandbox."""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.mark.asyncio
async def test_forecast_page_renders(client):
    res = await client.get("/forecast")
    assert res.status_code == 200
    body = res.text
    assert "Proyeksi Beban Kerja" in body
    assert "Rasio Kecukupan Antrean" in body
    assert "kasus / hari" in body


@pytest.mark.asyncio
async def test_crossdoc_page_form_renders(client):
    res = await client.get("/crossdoc")
    assert res.status_code == 200
    body = res.text
    assert "Nyandingan Dokumen Lintas Sumber" in body
    assert "Cocokkan Dokumen" in body


@pytest.mark.asyncio
async def test_crossdoc_post_critical_escalates(client):
    settings = get_settings()
    admin_key_header = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    created = await client.post(
        "/admin/company", json={"name": "PT Web CrossDoc", "npp": "7-7401"},
        headers=admin_key_header,
    )
    assert created.status_code == 200
    company_id = created.json()["company_id"]

    res = await client.post(
        "/crossdoc",
        data={
            "company_id": company_id,
            "period": "2026-09",
            "pph21_gross": "8000000",
            "payroll_gross": "5000000",
            "payroll_jkn": "50000",
            "bank_amounts": "4000000",
        },
    )
    assert res.status_code == 200
    body = res.text
    assert "KASUS DIESKALASI" in body
    assert "X1_PPH21_PAYROLL" in body
    assert "X3_BANK_PAYROLL" in body


@pytest.mark.asyncio
async def test_crossdoc_post_aligned_bundles(client):
    settings = get_settings()
    admin_key_header = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    created = await client.post(
        "/admin/company", json={"name": "PT Web Selaras", "npp": "7-7402"},
        headers=admin_key_header,
    )
    company_id = created.json()["company_id"]

    res = await client.post(
        "/crossdoc",
        data={
            "company_id": company_id,
            "period": "2026-10",
            "pph21_gross": "5000000",
            "payroll_gross": "5000000",
            "payroll_jkn": "50000",
            "bank_amounts": "4950000",
        },
    )
    assert res.status_code == 200
    body = res.text
    assert "Temuan Kritis" in body
    assert "KASUS DIESKALASI" not in body


@pytest.mark.asyncio
async def test_crossdoc_unknown_company_returns_error_banner(client):
    res = await client.post(
        "/crossdoc",
        data={"company_id": "c-tak-ada", "period": "2026-10"},
    )
    assert res.status_code == 400
    assert "Perusahaan tidak dikenal" in res.text


@pytest.mark.asyncio
async def test_sandbox_page_renders_patterns(client):
    res = await client.get("/sandbox")
    assert res.status_code == 200
    body = res.text
    for pattern_title in (
        "Manipulasi mutasi musiman",
        "Under-reporting bertahap",
        "Split entitas mikro",
        "Perusahaan patuh (kontrol)",
    ):
        assert pattern_title in body
