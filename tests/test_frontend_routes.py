"""
Tests for frontend routes: /portal, /case/{token}, /verify, and static assets.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import CaseFile
from app.db.session import AsyncSessionLocal
from app.main import create_app


@pytest.mark.asyncio
async def test_frontend_pages():
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test Static CSS & JS Assets
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

        # 2. Test /portal
        r = await client.get("/portal")
        assert r.status_code == 200
        assert "SELARAS" in r.text
        assert "PORTAL PETUGAS" in r.text
        assert "Target Pemulihan Iuran" in r.text
        assert "Buka Berkas" in r.text

        # 3. Test /verify
        r = await client.get("/verify")
        assert r.status_code == 200
        assert "Lab Verifikasi Slip" in r.text
        assert "Modus #2: Under-reporting Upah" in r.text
        assert "Jaminan Privasi UU PDP No. 27/2022" in r.text

        # 4. Test /case/{token} with real token from portal
        async with AsyncSessionLocal() as session:
            case_res = await session.execute(select(CaseFile).limit(1))
            case = case_res.scalar_one()

            # Generate active token
            import secrets
            from hashlib import sha256
            from datetime import datetime, timedelta

            test_token = secrets.token_urlsafe(16)
            case.access_token_hash = sha256(test_token.encode()).hexdigest()
            case.token_expires = datetime.utcnow() + timedelta(hours=24)
            await session.commit()

        r = await client.get(f"/case/{test_token}")
        assert r.status_code == 200
        assert "BERKAS PEMERIKSAAN LAPANGAN" in r.text
        assert "Potensi Gap Iuran JKN" in r.text
        assert "Perbandingan Triangulasi 3 Sumber Data" in r.text
        assert "Checklist Pemeriksaan Petugas" in r.text
        assert "Salin Checklist" in r.text
        assert "Ambil Kasus" in r.text

        # 5. Test Case Action: Ambil
        r_action = await client.post(
            f"/case/{test_token}/action",
            data={"action": "ambil", "reason": "Mulai pemeriksaan"},
        )
        assert r_action.status_code == 200
        assert r_action.json()["status"] == "ok"
        assert r_action.json()["new_status"] == "assigned"

        print("ALL FRONTEND ROUTE TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_frontend_pages())
