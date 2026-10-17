"""Bot guards: no guessing the employer, and one report per slip check."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import func, select

from app.bot.handlers import slip_flow
from app.db.models import Company, SlipCheck, WorkerConsent, WorkerReport
from app.db.session import AsyncSessionLocal
from app.slip.schema import PayslipExtraction


async def _make_check(user_id: int, company_id: str) -> str:
    worker_hash = slip_flow._worker_hash(user_id)
    check_id = str(uuid4())
    async with AsyncSessionLocal() as s:
        if not await s.get(WorkerConsent, worker_hash):
            s.add(WorkerConsent(worker_hash=worker_hash, consent_version="1.0"))
        s.add(SlipCheck(
            check_id=check_id, worker_hash=worker_hash, company_id=company_id, period="2026-09",
            verdict_codes='["DEDUCTION_LOW"]', expected_deduction=55_000, actual_deduction=30_000,
            implied_wage_base=3_000_000, rule_version="test",
        ))
        await s.commit()
    return check_id


def _callback(user_id: int, check_id: str):
    query = SimpleNamespace(
        data=f"action:report:{check_id}", from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query), query


async def _report_count(check_id: str) -> int:
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(func.count()).select_from(WorkerReport).where(WorkerReport.check_id == check_id)
        )).scalar()


async def _company_id() -> str:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Company.company_id).limit(1))).scalar_one()


async def test_repeated_report_taps_create_a_single_report(seeded_db):
    check_id = await _make_check(111, await _company_id())

    for _ in range(3):
        update, query = _callback(111, check_id)
        await slip_flow.handle_report_callback(update, None)

    assert await _report_count(check_id) == 1
    assert "sudah tercatat" in query.edit_message_text.call_args.args[0]


async def test_other_user_cannot_report_someone_elses_check(seeded_db):
    check_id = await _make_check(111, await _company_id())

    update, query = _callback(222, check_id)
    await slip_flow.handle_report_callback(update, None)

    assert await _report_count(check_id) == 0
    assert "tidak ditemukan" in query.edit_message_text.call_args.args[0]


async def test_report_evidence_uses_real_slip_numbers(seeded_db):
    check_id = await _make_check(111, await _company_id())
    update, _ = _callback(111, check_id)
    await slip_flow.handle_report_callback(update, None)

    async with AsyncSessionLocal() as s:
        company_id = (await s.get(SlipCheck, check_id)).company_id
        from app.db.models import CaseFile
        cases = (await s.execute(select(CaseFile).where(CaseFile.company_id == company_id))).scalars().all()
    import json
    evidence = json.loads(cases[0].reasons_json)[0]["evidence"]
    assert "30.000" in evidence and "55.000" in evidence and "5.500.000" in evidence
    assert "5.600.000" not in evidence and "38.000" not in evidence  # old hard-coded defaults


async def _photo_update(user_id: int):
    class _File:
        async def download_to_drive(self, path):
            with open(path, "wb") as f:
                f.write(b"fake-image-bytes")

    async def get_file():
        return _File()

    message = SimpleNamespace(
        photo=[SimpleNamespace(get_file=get_file)], document=None, reply_text=AsyncMock(),
    )
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id)), message


async def _run_photo_flow(monkeypatch, employer_name: str):
    worker_hash = slip_flow._worker_hash(333)
    async with AsyncSessionLocal() as s:
        s.add(WorkerConsent(worker_hash=worker_hash, consent_version="1.0"))
        await s.commit()

    extraction = PayslipExtraction.model_validate({
        "is_payslip": True,
        "employer_name": {"value": employer_name, "confidence": 0.95},
        "earnings": [{"label": "Gaji Pokok", "amount": 5_000_000, "confidence": 0.95}],
        "deductions": [{"label": "BPJS Kesehatan", "amount": 50_000, "confidence": 0.95}],
    })

    async def fake_extract(_):
        return extraction

    monkeypatch.setattr(slip_flow, "preprocess", lambda raw: (raw, None))
    monkeypatch.setattr(slip_flow, "extract_payslip", fake_extract)

    update, message = await _photo_update(333)
    await slip_flow.handle_photo(update, SimpleNamespace(user_data={}))
    return message


async def _check_count() -> int:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(SlipCheck))).scalar()


async def test_unknown_employer_is_not_replaced_by_a_random_company(seeded_db, monkeypatch):
    message = await _run_photo_flow(monkeypatch, "PT Perusahaan Fiktif Yang Tidak Ada")

    assert "/lapor" in message.reply_text.call_args.args[0]
    assert await _check_count() == 0  # nothing was attributed to any company


async def test_ambiguous_employer_name_asks_instead_of_guessing(seeded_db, monkeypatch):
    message = await _run_photo_flow(monkeypatch, "PT")  # matches every "PT ..." company

    assert "/lapor" in message.reply_text.call_args.args[0]
    assert await _check_count() == 0
