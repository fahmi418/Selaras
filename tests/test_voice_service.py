"""Uji Voice PANDAWA: PII redaksi, klasifikasi keluhan deterministik, rekonsiliasi vonis slip."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.config import get_rules
from app.voice.service import classify_complaint, clip_transcript, redact, reconcile_with_slip


@pytest.fixture()
def rules():
    return deepcopy(get_rules())


def test_redact_long_numbers_and_contacts(rules):
    messy = "NPP saya 3120058882560001, telp 0812-3456-7890, email saya a@bps.go.id"
    out = redact(messy, rules=rules)
    assert "3120058882560001" not in out
    assert "[NUMBER]" in out
    assert "[EMAIL]" in out


def test_redact_disabled_keeps_digits(rules):
    rules.voice = {**rules.voice, "redact_digits": False}
    assert "123456" in redact("123456", rules=rules)


def test_clip_transcript_respects_config_limit(rules):
    rules.voice = {**rules.voice, "max_transcript_chars": 12}
    assert len(clip_transcript("a" * 40, rules=rules)) == 12


def test_classify_potongan_lebih_is_w1(rules):
    code, hits = classify_complaint("kenapa bpjs saya dipotong lebih dari teman saya", rules=rules)
    assert code == "W1"
    assert hits


def test_classify_gaji_dilaporkan_rendah_is_w3(rules):
    code, _ = classify_complaint("gaji pokok di bpjs dilaporkan lebih rendah", rules=rules)
    assert code == "W3"


def test_classify_belum_setor_is_w4(rules):
    code, _ = classify_complaint("iuran bulan ini tidak disetor ke bpjs", rules=rules)
    assert code == "W4"


def test_classify_unknown_is_other(rules):
    code, hits = classify_complaint("mau tanya soal jadwal loket", rules=rules)
    assert code == "OTHER"
    assert hits == []


def test_classify_deterministic_on_multiple_hits(rules):
    noisy = "gaji pokok dilaporkan lebih rendah dan iuran tidak disetor"
    once = classify_complaint(noisy, rules=rules)
    twice = classify_complaint(noisy, rules=rules)
    assert once == twice


def test_reconcile_w4_with_missing_deduction_is_aligned(rules):
    notes = reconcile_with_slip("W4", ["DEDUCTION_MISSING"], rules=rules)
    assert any("DEDUCTION_MISSING" in note for note in notes)


def test_reconcile_unknown_category_wont_annotate(rules):
    assert reconcile_with_slip("OTHER", ["OK"], rules=rules) == []


def test_reconcile_without_slip_verdict_asks_manual_check(rules):
    notes = reconcile_with_slip("W1", [], rules=rules)
    assert any("cek manusia" in note for note in notes)
