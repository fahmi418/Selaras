"""
Tests for frontend routes: /portal, /case/{token}, /verify, and static assets.
Runs against the throwaway DB from conftest (seeded demo data), never the developer's selaras.db.
"""

import secrets
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import select

from app.db.models import CaseFile
from app.db.session import AsyncSessionLocal


async def test_static_assets(client):
    r = await client.get("/static/css/tokens.css")
    assert r.status_code == 200
    assert "--color-deep-teal" in r.text
    assert "--color-mint-pulse" in r.text

    r = await client.get("/static/css/components.css")
    assert r.status_code == 200
    assert ".btn-primary" in r.text
    assert ".stat-block" in r.text

    r = await client.get("/static/js/case.js")
    assert r.status_code == 200
    assert "checklistStorageKey" in r.text


async def test_portal_and_verify_pages(client):
    r = await client.get("/portal")
    assert r.status_code == 200
    assert "SELARAS" in r.text
    assert "PORTAL PETUGAS" in r.text
    assert "Target Pemulihan Iuran" in r.text
    assert "Buka Berkas" in r.text

    r = await client.get("/verify")
    assert r.status_code == 200
    assert "Lab Verifikasi Slip" in r.text
    assert "Modus #2: Under-reporting Upah" in r.text
    assert "Jaminan Privasi UU PDP No. 27/2022" in r.text


async def test_case_page_and_actions(client):
    async with AsyncSessionLocal() as session:
        case = (await session.execute(select(CaseFile).limit(1))).scalar_one()
        token = secrets.token_urlsafe(16)
        case.access_token_hash = sha256(token.encode()).hexdigest()
        case.token_expires = datetime.utcnow() + timedelta(hours=24)
        await session.commit()

    r = await client.get(f"/case/{token}")
    assert r.status_code == 200
    assert "BERKAS PEMERIKSAAN LAPANGAN" in r.text
    assert "Potensi Gap Iuran JKN" in r.text
    assert "Perbandingan Triangulasi 3 Sumber Data" in r.text
    assert "Checklist Pemeriksaan Petugas" in r.text
    assert "Salin Checklist" in r.text
    assert "Ambil Kasus" in r.text

    r = await client.post(f"/case/{token}/action", data={"action": "ambil", "reason": "Mulai pemeriksaan"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["new_status"] == "assigned"
