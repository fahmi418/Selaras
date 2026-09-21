# Taksonomi Modus Operandi & Sinyal Risiko

Dokumen ini menguraikan kelima modus operandi ketidakselarasan iuran JKN yang dideteksi oleh Selaras (berdasarkan PRD v1.1 §2.3, §10, dan §18.3), sinyal risiko matematis yang dipicu, serta rumus estimasi nilai iuran yang berpotensi hilang.

---

## 1. Lima Modus Operandi Utama

```
┌─────────┬──────────────────────────────────┬────────────────────────────────────────────────────────┐
│ Modus # │ Nama Modus                       │ Deskripsi Pola Praktik                                │
├─────────┼──────────────────────────────────┼────────────────────────────────────────────────────────┤
│ Modus 1 │ Pendaftaran Pekerja Sebagian     │ Badan usaha hanya mendaftarkan 40–80% dari total       │
│         │ (Partial Enrollment / Headcount) │ tenaga kerja aktif sebenarnya ke program JKN.         │
├─────────┼──────────────────────────────────┼────────────────────────────────────────────────────────┤
│ Modus 2 │ Under-reporting Upah             │ Upah yang dilaporkan ke BPJS Kesehatan ditekan 30–60% │
│         │ (Wage Base Suppression)          │ lebih rendah dari gaji riil pada slip pekerja.        │
├─────────┼──────────────────────────────────┼────────────────────────────────────────────────────────┤
│ Modus 3 │ Penggelapan Iuran                │ Slip gaji memotong 1% dari pekerja, namun pemberi      │
│         │ (Dipotong tapi Tak Disetor)      │ kerja menunggak atau tidak menyetorkan porsi 4%+1%.   │
├─────────┼──────────────────────────────────┼────────────────────────────────────────────────────────┤
│ Modus 4 │ Misklasifikasi Status Kerja      │ Pekerja tetap/kontrak diadministrasikan sebagai mitra  │
│         │ (False Independent Contractor)   │ lepas untuk menghindari kewajiban iuran 4% badan usaha.│
├─────────┼──────────────────────────────────┼────────────────────────────────────────────────────────┤
│ Modus 5 │ Manipulasi Data Mutasi           │ Pendaftaran kepesertaan ditunda berbulan-bulan sejak   │
│         │ (Delayed Registration & Churn)   │ masa kerja dimulai, atau mutasi keluar terlambat.     │
└─────────┴──────────────────────────────────┴────────────────────────────────────────────────────────┘
```

---

## 2. Taksonomi Sinyal Risiko (C1 – C6)

Setiap malam pada pukul 05:00, modul `app/risk/features.py` mengagregasikan metrik operasional menjadi 6 sinyal risiko terstandarisasi (nilai $0.0$ hingga $1.0$):

### Sinyal C1 — Under-Reporting Upah
- **Definisi:** Rasio perbandingan rata-rata upah terlapor dengan upah tersirat dari slip gaji dan benchmark sektor.
- **Formula:**
  $$\text{Gap Ratio} = \frac{\text{Upah Tersirat Slip} - \text{Upah Terlapor BPJS}}{\text{Upah Tersirat Slip}}$$
  Jika $\text{Gap Ratio} > 0.1$, sinyal C1 diaktifkan proporsional hingga $1.0$.
- **Terkait:** Modus #2.

### Sinyal C2 — Gap Tenaga Kerja (Headcount)
- **Definisi:** Perbedaan antara estimasi pekerja aktif (berdasarkan ukuran usaha, data presensi, atau agregat laporan) dengan jumlah pekerja terdaftar pada enrollment BPJS.
- **Formula:**
  $$C2 = \min\left(1.0, \; \frac{\text{Est. Headcount} - \text{Registered Headcount}}{\text{Est. Headcount}}\right)$$
- **Terkait:** Modus #1.

### Sinyal C3 — Inkonsistensi Penyetoran Billing
- **Definisi:** Adanya potongan pada slip gaji pekerja namun catatan billing pembayaran iuran 4%+1% menunggak pada 1–6 bulan terakhir.
- **Formula:**
  $$C3 = \frac{\text{Jumlah Bulan Menunggak}}{6} \times \text{Bobot Slip Potong}$$
- **Terkait:** Modus #3.

### Sinyal C4 — Penyeragaman Upah Buatan
- **Definisi:** Deviasi standar upah pekerja yang dilaporkan mendekati nol (misal 95%+ pekerja dari level operator hingga manajer dilaporkan tepat di angka UMK).
- **Formula:**
  $$C4 = 1.0 - \min\left(1.0, \; \frac{\sigma(\text{Upah Terdaftar})}{\mu(\text{Upah Terdaftar}) \times 0.25}\right)$$
- **Terkait:** Modus #2 (Penyeragaman ke batas minimum).

### Sinyal C5 — Anomali Mutasi & Masa Tunggu
- **Definisi:** Keterlambatan tanggal mulai terdaftar (*since*) dibanding tanggal mulai kerja aktual, atau rasio keluar-masuk yang tidak wajar dibanding rata-rata sektor.
- **Terkait:** Modus #5.

### Sinyal C6 — Laporan Mandiri Terverifikasi
- **Definisi:** Jumlah laporan independen yang diajukan oleh pekerja aktif yang lolos verifikasi rule engine.
- **Formula:**
  $$C6 = \min\left(1.0, \; \frac{\text{Laporan Terverifikasi}}{k}\right), \quad k = 3$$
- **Terkait:** Seluruh Modus.

---

## 3. Estimasi Potensi Iuran Hilang

Untuk setiap badan usaha yang terindikasi anomali, modul `app/risk/estimator.py` menghitung rentang nilai rupiah iuran yang belum terserap per bulan:

$$\text{Tarif Total} = 5\% \; (4\% \text{ Badan Usaha} + 1\% \text{ Pekerja})$$

### 1. Estimasi Modus #1 (Headcount Gap)
$$\text{Est. Gap}_1 = (\text{Est. Headcount} - \text{Registered Headcount}) \times \text{Dasar Upah Wajar} \times 0.05$$

### 2. Estimasi Modus #2 (Under-reporting)
$$\text{Est. Gap}_2 = \text{Registered Headcount} \times (\text{Upah Riil} - \text{Upah Terlapor}) \times 0.05$$

### 3. Estimasi Modus #3 (Penggelapan Setoran)
$$\text{Est. Gap}_3 = \sum_{\text{Bulan Nunggak}} \text{Billed Amount}$$

### 4. Rentang Ketidakpastian (P25 – P75)
Sistem tidak memberikan satu angka mutlak, melainkan rentang probabilistik:
- **`est_low` (Batas Bawah P25):** Estimasi konservatif hanya berdasarkan selisih minimal terkonfirmasi.
- **`est_mid` (Nilai Median):** Estimasi titik tengah paling representatif yang digunakan untuk prioritisasi antrean knapsack.
- **`est_high` (Batas Atas P75):** Proyeksi maksimal jika pelanggaran mencakup seluruh tenaga kerja di lokasi.
- **Proyeksi 12 Bulan:** $\text{est\_mid} \times 12$ untuk memberikan gambaran kerugian fiskal tahunan pada program JKN.
