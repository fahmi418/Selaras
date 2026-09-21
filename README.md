# SELARAS

> **Sistem Deteksi Ketidakselarasan Iuran JKN Berbasis Triangulasi Tiga Titik Data (Pekerja – Badan Usaha – Patokan Wilayah) dengan Alur Kerja Optimal Petugas Pemeriksa Lapangan.**

---

## Ringkasan Masalah & Solusi

Asimetri informasi antara apa yang **dipotong dari gaji riil pekerja** dan apa yang **dilaporkan oleh badan usaha ke BPJS Kesehatan** menjadi celah utama kebocoran dana jaminan sosial kesehatan:
1. **Under-reporting Upah (Modus #2):** Gaji riil pekerja Rp5.500.000, namun didaftarkan ke BPJS hanya sebesar UMK Rp3.500.000.
2. **Pendaftaran Sebagian (Modus #1):** Perusahaan mempekerjakan 200 orang, namun hanya 120 orang yang didaftarkan ke program JKN.
3. **Penggelapan Iuran (Modus #3):** Potongan 1% tercantum pada slip gaji pekerja, namun iuran 4%+1% tidak disetorkan atau menunggak di kas BPJS.

**Selaras hadir sebagai solusi triangulasi independen:**
Menggabungkan data ekstraksi slip gaji pekerja (*privacy-by-design*), data historis billing & kepesertaan badan usaha, serta patokan upah minimum regional (UMK) & benchmark sektor. Sistem memprioritaskan entitas berisiko tinggi dan menyusun antrean harian berbasis kuota jam kerja petugas (*greedy knapsack*) untuk memaksimalkan pemulihan iuran.

---

## Fitur & Keunggulan Utama

- **Triangulasi 3 Titik Objektif:** Memverifikasi keselarasan upah riil, catatan administrasi, dan patokan regulasi wilayah tanpa mengandalkan satu sumber tunggal.
- **Rule Engine Deterministik (Python Pure Logic):** Menggunakan aturan pasti PP 86/2013 & Perpres 82/2018. LLM **hanya** mengekstrak teks; Python menghitung seluruh angka dan vonis diskrepansi (*explainable & non-hallucinatory*).
- **Perlindungan Privasi Ketat (UU PDP No. 27/2022):** Foto slip gaji segera dihapus permanen setelah ekstraksi (maks. 10 menit). Identitas pekerja pseudonim (`worker_hash`) tidak pernah bocor ke lapisan petugas maupun dashboard.
- **Mesin Skoring Gabungan (Hybrid Scoring):**
  - **70% Rule Score:** Sinyal C1 s/d C6 (under-reporting, gap headcount, inkonsistensi setoran, penyeragaman upah, mutasi, laporan terverifikasi).
  - **30% Anomaly Score:** Model *IsolationForest* mendeteksi anomali multivariat terhadap benchmark sektor.
- **Penyusun Antrean Berkapasitas (Capacity-Aware Knapsack Planner):** Mengoptimalkan penugasan kasus harian agar sesuai dengan kuota kerja petugas (misal: 8.0 jam/hari), dengan alokasi 20% slot eksplorasi acak.
- **Antarmuka Petugas Lapangan Mobile-First (`DESIGN.md`):**
  - Mengadopsi estetika *Clinical Apothecary* dengan palet Deep Teal (`#244d54`), Mint Pulse (`#2ecea0`), Soft Teal (`#6dddbd`), dan tipografi `Inter Tight`.
  - **100% SVG Monokrom:** Bebas emoji, mengedepankan visual klinis profesional.
  - **8 Elemen Wajib PRD §14.1:** Header identitas, ringkasan 1 kalimat, stat block estimasi iuran (median, rentang, 12 bulan), 3 kartu alasan risiko, tabel bukti triangulasi & mini bar, checklist interaktif dengan penyimpanan lokal & tombol salin WhatsApp, riwayat kasus, dan sticky mobile action dock dengan drawer hasil kunjungan.
- **Portal Antrean Petugas (`/portal`) & Lab Verifikasi Slip (`/verify`):** Dilengkapi preset skenario modus 1–3 untuk pengujian instan.
- **Dashboard Monitoring Eksekutif (Streamlit 5 Tab):** KPI ringkasan, antrean kasus terfilter, sebaran wilayah, simulasi evaluasi sintetis, dan panel admin.

---

## Arsitektur Sistem

```
[Pekerja / Publik] ───► [Bot Telegram / Web Verify]
                                 │ (Unggah Foto Slip)
                                 ▼
                     [Pipeline Ekstraksi Slip]
               PIL Deskew → Gemini VLM → Pydantic
                                 │
                                 ▼
                   [Rule Engine Deterministik]
                 Hitung Dasar Upah, Potongan 1%,
                  Vonis (OK, DEDUCTION_LOW, dll)
                                 │
                                 ▼
                       [Basis Data SQLite/PG]
                  (Pemisahan Data Pekerja & Badan Usaha)
                                 │ (Batch Harian)
                                 ▼
                     [Feature Builder & Scoring]
                     Rule (70%) + IsoForest (30%)
                                 │
                                 ▼
                      [Knapsack Queue Planner]
                     Optimasi Kuota Jam Petugas
                                 │
                                 ▼
                     [Tautan Kasus Bertoken]
                                 │
            ┌────────────────────┴────────────────────┐
            ▼                                         ▼
   [Portal Antrean Web]                      [Halaman Kasus Mobile]
    (http://.../portal)                       (http://.../case/{token})
```

---

## Struktur Direktori Repositori

```
Selaras/
├── app/
│   ├── main.py                    # FastAPI application factory & lifespan
│   ├── config.py                  # Pydantic BaseSettings & rules.yaml loader
│   ├── audit.py                   # Append-only audit logger
│   ├── bot/                       # Telegram Bot integration (python-telegram-bot)
│   ├── slip/                      # Preprocessing & Gemini VLM extraction
│   ├── rules/                     # Deterministic rule engine & dictionary
│   │   ├── engine.py              # Verdict decision tree & calculations
│   │   ├── dictionary.py          # Wage component classifier
│   │   └── tests/                 # 39 unit tests untuk rule engine
│   ├── risk/                      # Features builder, scoring & iuran gap estimator
│   ├── queue/                     # Greedy knapsack planner & morning notifier
│   ├── web/                       # Case page, portal, verify routes & templates
│   │   ├── routes.py              # Web router & token access validation
│   │   ├── templates/             # HTML templates (case.html, portal.html, verify.html)
│   │   └── static/                # CSS tokens, components, and client-side JS
│   ├── api/                       # Internal & admin JSON endpoints
│   ├── db/                        # SQLAlchemy async models & session management
│   └── jobs/                      # APScheduler scheduled cron jobs
├── config/
│   └── rules.yaml                 # Versi aturan bisnis & parameter ambang
├── dashboard/
│   └── app.py                     # Streamlit Executive Dashboard (5 tab)
├── synth/
│   ├── generator.py               # Synthetic dataset generator berlabel modus
│   └── seed_demo.py               # Seeder demonstrasi lokal (petugas & kasus aktif)
├── eval/
│   └── run_eval.py                # Simulasi 30 hari & metrik evaluasi (lift/precision)
├── docs/
│   ├── architecture.md            # Dokumentasi arsitektur sistem & alur data
│   ├── modi_operandi.md           # Rincian modus operandi & sinyal risiko C1–C6
│   ├── setup.md                   # Panduan instalasi lokal & Docker
│   └── api.md                     # Spesifikasi endpoint API & cURL
├── tests/
│   └── test_frontend_routes.py    # Integrasi endpoint frontend & aset statis
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Panduan Memulai Cepat (Quick Start)

### 1. Prasyarat & Instalasi
Pastikan terpasang **Python 3.10+**.

```bash
# Buat & aktifkan virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Install dependensi
pip install -e ".[dev]"
```

### 2. Konfigurasi Environment
```bash
cp .env.example .env
```
*(File `.env` sudah terisi dengan nilai default yang aman untuk demo lokal).*

### 3. Inisialisasi Basis Data Demonstrasi
Isi basis data dengan 4 petugas wilayah, 6 badan usaha berisiko dengan berbagai modus (Modus 1, 2, 3), antrean kasus aktif, dan riwayat kunjungan:
```bash
python -m synth.seed_demo
```

### 4. Jalankan Server FastAPI & Antarmuka Web
```bash
python -m uvicorn app.main:create_app --factory --port 8000 --reload
```

Buka di browser:
- **Portal Antrean Petugas:** [http://127.0.0.1:8000/portal](http://127.0.0.1:8000/portal)
- **Lab Verifikasi Slip Gaji:** [http://127.0.0.1:8000/verify](http://127.0.0.1:8000/verify)
- **Swagger / OpenAPI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### 5. Jalankan Dashboard Eksekutif Streamlit (Opsional)
```bash
streamlit run dashboard/app.py --server.port 8501
```
Buka di browser: [http://localhost:8501](http://localhost:8501)

---

## Pengujian Otomatis

Seluruh logika bisnis, kalkulasi upah, pohon keputusan, dan rute web diuji secara ketat:

```bash
pytest tests/ app/rules/tests/ -v
```

**Hasil Pengujian:**
```
tests/test_frontend_routes.py::test_frontend_pages PASSED                [  2%]
app/rules/tests/test_engine.py::test_canonical_ok PASSED                 [  5%]
...
app/rules/tests/test_engine.py::test_umk_floor_parametric PASSED         [100%]

============================= 40 passed in 1.15s ==============================
```

---

## Penafian Hukum & Etika (UU PDP No. 27/2022)

1. **Bukan Alat Penghukuman Otomatis:** Skor risiko Selaras adalah indikasi awal probabilistik untuk membantu prioritisasi jadwal kunjungan petugas, bukan bukti pelanggaran hukum.
2. **Kerahasiaan Pelapor:** Selaras tidak membeberkan identitas, foto slip asli, maupun waktu presisi laporan pekerja kepada pihak perusahaan maupun petugas pemeriksa.
3. **Data Demo 100% Sintetis:** Seluruh nama perusahaan, NPP, upah, dan entitas dalam demonstrasi ini adalah fiktif dan dibuat secara algoritmik untuk tujuan pengujian.

---

## Dokumentasi Lengkap

- [Arsitektur Sistem & Alur Triangulasi](file:///d:/Ethermind_Agency/Selaras/docs/architecture.md)
- [Taksonomi Modus Operandi & Sinyal C1–C6](file:///d:/Ethermind_Agency/Selaras/docs/modi_operandi.md)
- [Panduan Pengaturan Lingkungan & Deployment](file:///d:/Ethermind_Agency/Selaras/docs/setup.md)
- [Spesifikasi Lengkap API Endpoints](file:///d:/Ethermind_Agency/Selaras/docs/api.md)
- [Design Tokens & Style Reference](file:///d:/Ethermind_Agency/Selaras/DESIGN.md)
