# Selaras API Documentation

> **Base URL:** `https://your-domain.example.com`  
> **Mode Demo:** semua endpoint tersedia; gunakan data sintetis.

---

## Authentication

| Endpoint Group | Header | Value |
|---|---|---|
| Internal API (`/api/*`) | `X-API-Key` | `INTERNAL_API_KEY` dari `.env` |
| Admin API (`/admin/*`) | `X-API-Key` | `ADMIN_API_KEY` dari `.env` |
| Case Page (`/case/*`) | URL token (path param) | Token 128-bit sekali pakai |
| Webhook (`/webhook/telegram`) | `X-Telegram-Bot-Api-Secret-Token` | `TELEGRAM_WEBHOOK_SECRET` |

---

## Endpoints

### `GET /health`
Liveness check. Public, no auth.

**Response `200`:**
```json
{"status": "ok", "service": "selaras-backend"}
```

---

### `POST /webhook/telegram`
Menerima update dari Telegram Bot API.

**Auth:** `X-Telegram-Bot-Api-Secret-Token` header  
**Body:** Telegram Update JSON (dikirim otomatis oleh Telegram)  
**Response `200`:** `{"ok": true}`

---

### `GET /case/{token}`
Menampilkan halaman kasus petugas (HTML).

**Auth:** Token 128-bit dalam URL path (valid 24 jam, dikirim lewat pesan pagi Telegram)  
**Response:** HTML page dengan detail kasus, alasan, estimasi, checklist

**Error:**
- `403` — token tidak valid
- `410` — token kedaluwarsa

---

### `POST /case/{token}/action`
Mengambil aksi terhadap kasus.

**Auth:** Token (sama dengan GET)  
**Body (form-data):**
```
action: ambil | tunda | bukan_prioritas
reason: string (opsional)
```

**Response `200`:**
```json
{
  "status": "ok",
  "case_id": "uuid",
  "new_status": "assigned"
}
```

> **Catatan:** Aksi tulis (`ambil`, `tunda`, `bukan_prioritas`) menginvalidasi token (sekali pakai untuk write).

**Error:** `400` — aksi tidak valid; `403/410` — token invalid/expired

---

### `POST /case/{token}/outcome`
Submit hasil kunjungan.

**Auth:** Token  
**Body (form-data):**
```
result:            TERBUKTI | TIDAK_TERBUKTI | PERLU_TINDAK_LANJUT
confirmed_modus:   1 | 2 | 3 | 4 | 5 (opsional)
found_amount:      integer Rp (opsional)
note:              string (opsional)
```

**Response `200`:**
```json
{"status": "ok", "outcome_id": "uuid"}
```

---

### `GET /portal`
Menampilkan portal antrean pemeriksaan lapangan petugas (HTML).

**Auth:** Publik / Demo mode (mendukung query param `?officer_id={uuid}`)  
**Response:** Halaman web responsif dengan beban kerja petugas, kuota jam harian, dan tautan berkas kasus.

---

### `GET /verify`
Menampilkan antarmuka lab verifikasi slip gaji publik/pekerja (HTML).

**Auth:** Publik (tanpa auth)  
**Response:** Halaman web interaktif split-hero untuk pengujian skenario modus 1–3, kalkulasi upah riil, dan trigger laporan anonim.

---

### `GET /api/queue`
Mengembalikan antrian kasus untuk seorang petugas.

**Auth:** `X-API-Key: INTERNAL_API_KEY`  
**Query params:**
- `officer_id` *(required)* — officer UUID
- `as_of` *(optional)* — date `YYYY-MM-DD`, default hari ini

**Response `200`:**
```json
{
  "officer_id": "uuid",
  "date": "2026-09-21",
  "cases": [
    {
      "case_id": "uuid",
      "company_name": "PT Maju Bersama 01",
      "company_id": "uuid",
      "priority": 9432.5,
      "status": "assigned",
      "due_date": "2026-09-22"
    }
  ]
}
```

---

### `GET /api/company/{company_id}/risk`
Mengembalikan skor risiko terbaru untuk sebuah perusahaan.

**Auth:** `X-API-Key: INTERNAL_API_KEY`

**Response `200`:**
```json
{
  "company_id": "uuid",
  "as_of": "2026-09-21",
  "risk": 78.3,
  "risk_label": "Tinggi",
  "rule_score": 0.743,
  "anomaly_score": 0.812,
  "signals": {
    "C1_under_reporting": 0.65,
    "C2_headcount_gap": 0.48,
    "C3_remit_inconsistent": 0.30,
    "C4_uniform_wage": 0.10,
    "C5_churn": 0.05,
    "C6_worker_reports": 0.90
  },
  "est_low": 18000000,
  "est_mid": 25000000,
  "est_high": 38000000,
  "p_valid": 0.783,
  "model_version": "rule+isoforest-v1"
}
```

**Error:** `404` — perusahaan belum pernah di-score

---

### `POST /api/slip/verify`
Verifikasi slip yang sudah diekstrak terhadap rule engine. Berguna untuk testing dan integrasi.

**Auth:** `X-API-Key: INTERNAL_API_KEY`  
**Body (JSON):**
```json
{
  "extraction": {
    "is_payslip": true,
    "period": {"month": 9, "year": 2026, "confidence": 0.99},
    "earnings": [
      {"label": "Gaji Pokok", "amount": 4500000, "confidence": 0.99},
      {"label": "Tunj. Jabatan", "amount": 1000000, "confidence": 0.99}
    ],
    "deductions": [
      {"label": "BPJS Kesehatan 1%", "amount": 30000, "confidence": 0.99}
    ]
  },
  "company_id": "uuid",
  "umk": 3000000,
  "recorded_wage_base": 3000000,
  "is_remitted": false,
  "is_registered": true
}
```

**Response `200`:**
```json
{
  "verdicts": ["DEDUCTION_LOW", "WAGE_BASE_MISMATCH", "NOT_REMITTED"],
  "expected_deduction": 55000,
  "actual_deduction": 30000,
  "implied_wage_base": 3000000,
  "wage_base_used": 5500000,
  "explanation": "Potongan di slip (Rp30.000) lebih kecil dari seharusnya (Rp55.000 untuk upah dasar Rp5.500.000). Setoran iuran bulan ini belum tercatat di BPJS.",
  "confidence": "high",
  "rule_version": "2026.09.1"
}
```

---

### `POST /admin/synth/reset`
Reset semua data sintetis dan generate ulang (seed tetap = 42).

**Auth:** `X-API-Key: ADMIN_API_KEY`  
**Hanya tersedia saat `DEMO_MODE=true`**

**Response `200`:**
```json
{"status": "ok", "companies_loaded": 2000}
```

---

### `POST /admin/jobs/run/{job}`
Trigger job terjadwal secara manual.

**Auth:** `X-API-Key: ADMIN_API_KEY`  
**Path param `job`:** `features` | `scoring` | `queue` | `morning_message`

**Response `200`:**
```json
{"status": "ok", "job": "scoring", "result": {"scored": 2000}}
```

---

## Verdict Codes

| Kode | Arti | Sinyal |
|---|---|---|
| `OK` | Semua selaras | – |
| `DEDUCTION_LOW` | Potongan di slip < seharusnya | W1 |
| `DEDUCTION_MISSING` | Tidak ada potongan JKN di slip | W2 |
| `DEDUCTION_HIGH` | Potongan > seharusnya (info) | – |
| `WAGE_BASE_MISMATCH` | Upah tersirat dari slip ≠ upah tercatat BPJS | W3 |
| `NOT_REMITTED` | Potongan ada, setoran belum tercatat | W4 |
| `NOT_REGISTERED` | Pekerja tidak ditemukan di kepesertaan | W5 |
| `BELOW_UMK` | Upah dasar < UMK (info, bukan fraud JKN) | W6 |
| `NEEDS_REVIEW` | Confidence rendah / komponen ambigu | – |

---

## Error Responses

Semua error mengikuti format FastAPI standar:

```json
{"detail": "Pesan error dalam Bahasa Indonesia."}
```

| HTTP | Kondisi |
|---|---|
| `400` | Input tidak valid |
| `403` | API key salah / token tidak valid |
| `404` | Resource tidak ditemukan |
| `410` | Token kedaluwarsa |
| `422` | Validasi Pydantic gagal |
| `500` | Internal server error |

---

## cURL Examples

```bash
# Health check
curl https://your-domain.example.com/health

# Verify slip via API
curl -X POST https://your-domain.example.com/api/slip/verify \
  -H "X-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"extraction": {"is_payslip": true, "earnings": [{"label":"Gaji Pokok","amount":5000000,"confidence":0.99}], "deductions": [{"label":"BPJS Kesehatan","amount":50000,"confidence":0.99}]}, "umk": 3000000}'

# Get company risk
curl https://your-domain.example.com/api/company/COMPANY_UUID/risk \
  -H "X-API-Key: $INTERNAL_API_KEY"

# Trigger morning message manually (for demo)
curl -X POST https://your-domain.example.com/admin/jobs/run/morning_message \
  -H "X-API-Key: $ADMIN_API_KEY"

# Reset demo data
curl -X POST https://your-domain.example.com/admin/synth/reset \
  -H "X-API-Key: $ADMIN_API_KEY"
```
