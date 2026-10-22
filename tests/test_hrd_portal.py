"""Tests portal HRD Fase 2: login NPP, dashboard,imulator what-if, penjaga sesi."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_hrd_requires_login(client):
    response = await client.get("/hrd/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/hrd"


@pytest.mark.asyncio
async def test_hrd_login_unknown_npp(client):
    response = await client.post("/hrd/login", data={"npp": "9-99999999"}, follow_redirects=False)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_hrd_full_session_flow(client):
    # Registrasi perusahaan melalui admin agar NPP dikenal.
    settings_key = (await _settings()).admin_api_key.get_secret_value()
    created = await client.post(
        "/admin/company",
        json={"name": "PT Hrd Sejahtera", "npp": "6-6001"},
        headers={"X-API-Key": settings_key},
    )
    assert created.status_code == 200
    company_id = created.json()["company_id"]

    login = await client.post("/hrd/login", data={"npp": "6-6001"}, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/hrd/dashboard"

    dashboard = await client.get("/hrd/dashboard")
    assert dashboard.status_code == 200
    assert "PT Hrd Sejahtera" in dashboard.text
    assert "Simulator deklarasi mandiri" in dashboard.text

    # Perusahaan baru belum punya snapshot fitur: simulator menampilkan pesan, bukan crash.
    what_if = await client.post(
        "/hrd/what-if",
        data={"declared_new_workers": "5", "declare_benchmark_wage": "true"},
    )
    assert what_if.status_code == 200
    assert "PT Hrd Sejahtera" in what_if.text
    _ = company_id


async def _settings():
    from app.config import get_settings

    return get_settings()
