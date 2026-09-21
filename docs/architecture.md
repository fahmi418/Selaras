# Selaras — Arsitektur Sistem

Dokumen ini menjelaskan arsitektur teknis sistem **Selaras**, prinsip triangulasi data, proteksi privasi (*Privacy-by-Design*), dan alur kerja end-to-end dari bot pekerja hingga tindakan petugas lapangan.

---

## 1. Konsep Inti: Triangulasi Tiga Titik

Selaras memecahkan asimetri informasi iuran JKN melalui pembuktian silang antara tiga sumber data independen:

```
                  [1. Sisi Pekerja]
              Slip Gaji Riil / Laporan Mandiri
                 (Gaji Pokok, Tunjangan Tetap,
                   Potongan 1% yang tertera)
                           ▲
                          / \
                         /   \
                        /     \
                       ▼       ▼
       [2. Sisi Badan Usaha] ◄──► [3. Sisi Wilayah & Sektor]
      Data Kepesertaan & Setoran       Patokan UMK Daerah &
        (Upah Tercatat, Status         Distribusi Upah Sektor
        Peserta, Riwayat Billing)      (Median, P25, P75)
```

1. **Titik 1 (Pekerja):** Mengunggah slip gaji atau melaporkan secara anonim via Bot Telegram / Web. LLM Vision (Gemini 1.5 Flash) mengekstrak komponen terstruktur (hanya angka & label, gambar slip langsung dihapus ≤10 menit).
2. **Titik 2 (Badan Usaha):** Catatan kepesertaan yang dilaporkan pemberi kerja ke BPJS Kesehatan (upah terlapor, jumlah tenaga kerja terdaftar, dan riwayat setoran iuran 4%+1%).
3. **Titik 3 (Patokan/Benchmark):** Upah Minimum Kabupaten/Kota (UMK) yang berlaku secara hukum dan distribusi upah sektoral (BPS/Kemenaker) sebagai filter batas bawah dan detektor penyeragaman upah buatan.

---

## 2. Diagram Aliran Data & Komponen

```
                  ┌──────────────────────────────────────────────┐
                  │           ANTARMUKA PENGGUNA                 │
                  └──────────────────────┬───────────────────────┘
                                         │
                 ┌───────────────────────┼────────────────────────┐
                 ▼                       ▼                        ▼
        [Bot Telegram Pekerja]   [Lab Verifikasi Web]   [Portal & Kasus Petugas]
        (/start, upload slip)      (/verify - public)      (/portal, /case/{token})
                 │                       │                        ▲
                 ▼                       │                        │
       [Pipeline Ekstraksi]              │                        │
     (PIL Deskew → Gemini VLM            │                        │
      → Pydantic Validation)             │                        │
                 │                       │                        │
                 ▼                       ▼                        │
     ┌──────────────────────────────────────────────┐             │
     │      RULE ENGINE DETERMINISTIK (Python)      │             │
     │  - Hitung Dasar Upah (clamped UMK & 12jt)    │             │
     │  - Evaluasi Potongan 1% vs Slip              │             │
     │  - Verdict: OK, DEDUCTION_LOW, MISMATCH, dll │             │
     └──────────────────────┬───────────────────────┘             │
                            │                                     │
                            ▼                                     │
     ┌──────────────────────────────────────────────┐             │
     │            BASIS DATA TERPISAH               │             │
     │  - worker_consent (hash pseudonim)           │             │
     │  - slip_check (agregat field terstruktur)    │             │
     │  - company, enrollment, billing              │             │
     └──────────────────────┬───────────────────────┘             │
                            │ (Batch Harian 05:00)                │
                            ▼                                     │
     ┌──────────────────────────────────────────────┐             │
     │          FEATURE BUILDER & SCORING           │             │
     │  - Sinyal C1 s/d C6 (Aturan Bisnis: 70%)     │             │
     │  - IsolationForest Anomaly Score (ML: 30%)   │             │
     │  - Estimator Rentang Kerugian Iuran (Modus)  │             │
     └──────────────────────┬───────────────────────┘             │
                            │ (Batch Harian 05:45)                │
                            ▼                                     │
     ┌──────────────────────────────────────────────┐             │
     │          KNAPSACK QUEUE PLANNER              │             │
     │  - Optimasi Kuota Jam Petugas (8.0 Jam/Hari) ├─────────────┘
     │  - 20% Slot Eksplorasi Kasus Sedang          │
     │  - Generasi Token URL Akses 128-bit (24 Jam) │
     └──────────────────────────────────────────────┘
```

---

## 3. Komponen Arsitektur Utama

### 3.1 Pipeline Ekstraksi Slip Gaji (`app/slip/`)
- **Prinsip Kunci:** LLM **hanya** bertugas mengekstrak teks menjadi JSON terstruktur; LLM **dilarang** melakukan kalkulasi matematika atau menentukan vonis pelanggaran.
- **Pre-processing (`preprocessor.py`):** Menggunakan PIL untuk normalisasi orientasi, deskew rotasi (Hough Transform), konversi format gambar (HEIC/PNG/JPEG) ke RGB, dan penyesuaian kontras.
- **Gemini Client (`gemini_client.py`):** Structured Output dengan Pydantic Schema (`PayslipExtraction`), temperatur 0.1, mekanisme retry eksponensial 2x.
- **Validasi Integritas (`validator.py`):** Memverifikasi konsistensi internal slip: `Sum(Penghasilan) - Sum(Potongan) ≈ Gaji Bersih (±1%)`.

### 3.2 Rule Engine Deterministik (`app/rules/`)
- **Filosofi:** Aturan kepatuhan hukum BPJS Kesehatan bersifat pasti dan wajib dapat diaudit (*explainable*).
- **Kamus Komponen Upah (`dictionary.py`):** Mengklasifikasikan label slip gaji ke dalam:
  - *Upah Pokok*
  - *Tunjangan Tetap* (diperhitungkan ke dasar iuran)
  - *Tunjangan Tidak Tetap / Variabel* (dikecualikan)
  - *Potongan JKN* (kata kunci: "BPJS Kes", "JKN", "Askes", dsb.)
- **Pohon Keputusan (`engine.py`):**
  - Menghitung `dasar_upah = clamp(pokok + tunjangan_tetap, UMK, Rp12.000.000)`.
  - Menghitung `ekspektasi_potongan = round(0.01 * dasar_upah)`.
  - Membandingkan dengan potongan aktual slip menggunakan batas toleransi `max(Rp1.000, 2% ekspektasi)`.
  - Menghasilkan verdict codes: `OK`, `DEDUCTION_LOW`, `DEDUCTION_HIGH`, `DEDUCTION_MISSING`, `WAGE_BASE_MISMATCH`, `NOT_REMITTED`, `NOT_REGISTERED`, `BELOW_UMK`, `NEEDS_REVIEW`.

### 3.3 Mesin Skoring Gabungan (`app/risk/`)
- Menggabungkan pendekatan *Expert Rule-based* (70%) dan *Unsupervised Machine Learning* (30%):
  - **Rule Score (0–100):** Pembobotan linear sinyal kepatuhan C1 s/d C6 (under-reporting, gap headcount, inkonsistensi setoran, penyeragaman upah, anomali mutasi, dan laporan pekerja).
  - **Anomaly Score (0–100):** Menggunakan model `IsolationForest` dari Scikit-Learn dengan contamination 0.12 untuk mendeteksi deviasi multivariat tak terduga pada rasio upah terhadap sektor dan ukuran perusahaan.
  - **Estimator Rentang Nilai (`estimator.py`):** Menghasilkan batas bawah (P25), nilai tengah (median), dan batas atas (P75) potensi iuran yang belum terserap per modus.

### 3.4 Knapsack Queue Planner (`app/queue/planner.py`)
- Petugas pemeriksa lapangan memiliki keterbatasan kuota jam kerja harian (misal: 8.0 jam).
- Algoritma menyusun antrean berbasis **Greedy Knapsack Allocation**:
  $$\text{Priority Score} = \frac{P(\text{Valid}) \times \text{Est. Gap}}{\text{Waktu Kunjungan} + \text{Waktu Tempuh}} \times \text{Urgency}$$
- **20% Slot Eksplorasi:** 1 dari 5 slot dialokasikan untuk perusahaan berisiko sedang dengan pemilihan terbobot acak guna mencegah *bias konfirmasi* dan memastikan pengawasan merata.

---

## 4. Privacy-by-Design & Kepatuhan UU PDP No. 27/2022

Sistem Selaras dirancang dengan standar perlindungan data tertinggi:

| Prinsip | Implementasi Teknis |
|---|---|
| **Minimisasi Data** | Foto slip gaji dihapus dari disk/memori sementara segera setelah ekstraksi (maks. 10 menit). Hanya angka dan kategori komponen upah yang disimpan. |
| **Pemisahan Identitas** | Nilai `worker_hash` (pseudonim SHA-256 tersalt) tidak pernah diteruskan ke database petugas, API publik, maupun dashboard. |
| **K-Anonimitas Sinyal Laporan** | Laporan pekerja hanya dapat menaikkan sinyal risiko jika terdapat $\ge k$ laporan independen ($k=2$) atau dikuatkan oleh anomali data numerik badan usaha. |
| **Keamanan Tautan Berkas** | Tautan kasus petugas menggunakan token kriptografis 128-bit acak dengan hash SHA-256 satu arah di DB, masa berlaku maks 24 jam, dan hangus setelah tindakan tulis. |
| **Audit Log Tak Terhapus** | Setiap pembacaan berkas, persetujuan kasus, dan pencatatan hasil audit dicatat pada tabel `audit_log` (*append-only*). |
| **Penafian Integritas** | Setiap kartu kasus menampilkan penafian hukum: *"Skor adalah indikasi matematis, bukan vonis pelanggaran."* untuk menjaga asas praduga tak bersalah. |
