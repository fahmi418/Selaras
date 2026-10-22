"""Tests keamanan API: compare_digest, webhook secret, dan alur admin endpoint."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.internal import create_company, require_admin, require_internal
from app.config import get_settings


@pytest.mark.asyncio
async def test_internal_key_mismatch_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await require_internal(x_api_key="definitely-wrong-key")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_internal_key_match_passes():
    key = get_settings().internal_api_key.get_secret_value()
    assert await require_internal(x_api_key=key) is None


@pytest.mark.asyncio
async def test_admin_key_mismatch_rejected_and_match_passes():
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(x_api_key="not-admin")
    assert exc_info.value.status_code == 403

    admin_key = get_settings().admin_api_key.get_secret_value()
    assert await require_admin(x_api_key=admin_key) is None


@pytest.mark.asyncio
async def test_webhook_rejects_missing_secret(client):
    response = await client.post("/webhook/telegram", json={"update_id": 1})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_secret(client):
    response = await client.post(
        "/webhook/telegram",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_company_endpoint_guards(client):
    settings = get_settings()
    admin_key = settings.admin_api_key.get_secret_value()

    #Tanpa header — FastAPI menolak sebelum/hingga dependensi jalan (422 validasi header)
    response = await client.post("/admin/company", json={"name": "X", "npp": "5-1"})
    assert response.status_code in (403, 422)

    # Region tak dikenal
    response = await client.post(
        "/admin/company",
        json={"name": "PT Baru", "npp": "5-2", "region_id": "unknown-region"},
        headers={"X-API-Key": admin_key},
    )
    assert response.status_code == 400

    # Sukses
    response = await client.post(
        "/admin/company",
        json={"name": "PT Karir Sehat", "npp": "5-3", "registered_headcount": 25},
        headers={"X-API-Key": admin_key},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["company_id"].startswith("comp-")
    assert body["npp"] == "5-3"

    # NPP duplikat
    response = await client.post(
        "/admin/company",
        json={"name": "PT Kembar", "npp": "5-3"},
        headers={"X-API-Key": admin_key},
    )
    assert response.status_code == 409
