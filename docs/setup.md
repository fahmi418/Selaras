# Panduan Instalasi & Menjalankan Sistem (Setup Guide)

Dokumen ini menjelaskan langkah-langkah penyiapan lingkungan lokal, konfigurasi environment, inisialisasi basis data sintetis, dan menjalankan layanan Selaras.

---

## 1. Prasyarat Sistem

- **Python:** Versi 3.10 atau 3.11
- **Sistem Operasi:** Windows / Linux / macOS
- **Alat Opsional:** Docker & Docker Compose (untuk deployment kontainer), Make (untuk automasi perintah)

---

## 2. Instalasi Dependensi Lokal

### Langkah 1 — Kloning Repositori & Buat Virtual Environment
```bash
# Buat virtual environment
python -m venv .venv

# Aktifkan virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate
```

### Langkah 2 — Install Paket & Dependensi
```bash
pip install --upgrade pip
pip install -e ".[dev]"
```

---

## 3. Konfigurasi Lingkungan (`.env`)

Salin file contoh konfigurasi `.env.example` menjadi `.env`:

```bash
cp .env.example .env
```

Sesuaikan nilai variabel berikut pada file `.env`:

```ini
# Bot Telegram (Dapat diisi dummy saat mode demo)
TELEGRAM_BOT_TOKEN=123456789:AAFakeTokenForDevelopmentTestingOnly
TELEGRAM_WEBHOOK_SECRET=local-dev-secret-token

# Gemini LLM API (Google AI Studio)
GEMINI_API_KEY=AIzaSyYourActualOrDummyKey
GEMINI_MODEL=gemini-1.5-flash-latest

# Database SQLite (atau postgresql+asyncpg://...)
DATABASE_URL=sqlite+aiosqlite:///./selaras.db

# Domain Aplikasi
APP_BASE_URL=http://localhost:8000
SECRET_KEY=dev-secret-change-me-to-a-random-256-bit-hex-string

# Kunci Autentikasi API Internal & Admin
ADMIN_API_KEY=admin-dev-key-12345
INTERNAL_API_KEY=internal-dev-key-12345

# Masa Berlaku Token Berkas Kasus (Jam)
CASE_TOKEN_TTL_HOURS=24

# Mode Demo & Logging
DEMO_MODE=true
SYNTH_SEED=42
LOG_LEVEL=INFO
```

---

## 4. Inisialisasi Basis Data & Data Sintetis

Untuk memuat skema database dan data demonstrasi realistis (mencakup 4 petugas, 6 perusahaan berlabel modus 1–3, antrean kasus, dan riwayat audit):

```bash
# Jalankan seeder demo
python -m synth.seed_demo
```

Jika ingin menghasilkan dataset skala besar (misal 300 s/d 2.000 perusahaan untuk simulasi evaluasi):

```bash
python -m synth.generator --companies 300 --seed 42
```

---

## 5. Menjalankan Layanan

### A. Menjalankan FastAPI Server (Backend & Web Frontend)
Layanan ini melayani bot webhook, API internal/admin, halaman kasus petugas, portal antrean, dan lab verifikasi slip:

```bash
python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

Akses antarmuka web melalui browser:
- **Portal Antrean Petugas:** [http://127.0.0.1:8000/portal](http://127.0.0.1:8000/portal)
- **Lab Verifikasi Slip Gaji:** [http://127.0.0.1:8000/verify](http://127.0.0.1:8000/verify)
- **Dokumentasi OpenAPI / Swagger:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Health Check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### B. Menjalankan Dashboard Eksekutif (Streamlit)
Dashboard visual untuk monitoring manajer pengawasan BPJS Kesehatan dan dewan juri:

```bash
streamlit run dashboard/app.py --server.port 8501
```
Akses di: [http://localhost:8501](http://localhost:8501)

### C. Menjalankan Batch Job Manual via API
Anda dapat memicu job terjadwal kapan saja untuk demonstrasi:
```bash
# Trigger pembentukan fitur
curl -X POST http://localhost:8000/admin/jobs/run/features -H "X-API-Key: admin-dev-key-12345"

# Trigger perhitungan skor risiko
curl -X POST http://localhost:8000/admin/jobs/run/scoring -H "X-API-Key: admin-dev-key-12345"

# Trigger penyusunan antrean knapsack
curl -X POST http://localhost:8000/admin/jobs/run/queue -H "X-API-Key: admin-dev-key-12345"
```

---

## 6. Menjalankan Uji Otomatis & Evaluasi

### Menjalankan Pytest Suite
```bash
pytest tests/ app/rules/tests/ -v
```
Seluruh 40 tes unit dan rute web dipastikan lulus (100% PASS).

### Menjalankan Evaluasi Simulasi 30 Hari
Untuk mengukur metrik presisi, recall, dan lift rasio terhadap 3 baseline:
```bash
python -m eval.run_eval --days 30
```
Laporan evaluasi otomatis tersimpan pada `eval/report.md`.

---

## 7. Menjalankan Menggunakan Docker Compose

Jika ingin menjalankan seluruh sistem di dalam kontainer terisolasi:

```bash
# Bangun dan jalankan kontainer
docker-compose up --build -d

# Periksa status kontainer
docker-compose ps

# Matikan kontainer
docker-compose down
```
Layanan akan otomatis mengekspos port 8000 (FastAPI) dan port 8501 (Streamlit).
