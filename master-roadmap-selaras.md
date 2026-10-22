# MASTER ROADMAP INOVASI SELARAS v2.0
## Framework & Roadmap Pengawasan Iuran JKN Berbasis GenAI/ML Terintegrasi

---

## 🏛️ 1. Pendahuluan & Landasan Filosofis

Sistem **SELARAS (Sistem Selaras Deteksi Iuran JKN)** dikembangkan untuk mengatasi kebocoran finansial dan ketidakpatuhan pemberi kerja dalam program JKN. Sesuai arahan Direktur TI BPJS Kesehatan dan Asosiasi Manajemen Data Indonesia (AMDI) pada **Healthkathon 2026**, pemanfaatan teknologi AI diarahkan pada pendekatan ***problem-first*** yang berfokus pada:
1. **Deteksi Dini Risiko & Fraud:** Mengidentifikasi manipulasi upah (*under-reporting*), pemotongan iuran tanpa penyetoran, dan ketidaksesuaian jumlah pekerja (*headcount gap*).
2. **AI Tepat Guna & Preskriptif:** AI tidak sekadar memberikan prediksi atau skor acak, melainkan memberikan draf rekomendasi tindakan operasional yang konkret.
3. **Human-in-the-Loop & Akuntabilitas:** AI berfungsi strictly sebagai pendukung keputusan (*decision support system*), sedangkan otoritas penetapan sanksi dan pemeriksaan tetap berada di tangan petugas Wasrik (Pengawasan dan Pemeriksaan) BPJS Kesehatan.
4. **Kepatuhan Privasi Ketat:** Memenuhi standar UU Perlindungan Data Pribadi (UU PDP No. 27/2022) melalui mekanisme anonimisasi (*one-way salted hash*) dan pemusnahan foto slip gaji (*Zero Raw Image Retention* dalam ≤10 menit).

---

## 📊 2. Baseline Arsitektur SELARAS v1.0 (Lolos Top 145)

Arsitektur SELARAS v1.0 yang telah teruji dan siap produksi (MVP) mengusung prinsip **"LLM Membaca, Kode Menghitung"**:
* **Triangulasi 3 Titik Data:**
  1. *Laporan Badan Usaha:* Data billing dan kepesertaan historis dari BPJS Kesehatan.
  2. *Data Riil Pekerja:* Ekstraksi slip gaji pekerja via kanal chat Telegram/WhatsApp menggunakan Multimodal Vision-Language Model (Gemini VLM).
  3. *Patokan Eksternal:* Data UMK regional dan benchmark sektor industri.
* **Deterministic Rule Engine (Python):** Menghitung selisih iuran dan vonis ketidakselarasan berdasarkan regulasi resmi (PP 86/2013 & Perpres 82/2018) tanpa risiko halusinasi AI.
* **Greedy Knapsack Queue Planner:** Mengoptimalkan antrean kunjungan harian petugas Wasrik berdasarkan kuota jam kerja operasional (kapasitas harian) dengan alokasi 20% slot eksplorasi acak.

---

## 🚀 3. Lima Pilar Inovasi GenAI/ML v2.0

### 📍 PILAR 1: Agentic Wasrik Copilot & Prescriptive Auditing
* **Deskripsi:** Mengembangkan AI dari sekadar pemberi skor menjadi *Task-Specific AI Agent* otonom yang memandu alur kerja pemeriksaan petugas lapangan secara *end-to-end*.
* **Komponen Fitur:**
  * **Pre-Audit Briefing Generator:** Secara otomatis merangkum profil risiko perusahaan, menganalisis pola anomali historis, dan menyusun draf strategi wawancara audit sebelum petugas turun ke lapangan.
  * **Prescriptive Action Recommendation:** Memberikan saran langkah pemeriksaan spesifik (misal: *"Saran tindakan: Minta bukti potong PPh 21 dan sampel rekening koran untuk 15 pekerja lini produksi 3 bulan terakhir"*).
  * **Voice-to-BAP Assistant:** Petugas merekam *voice note* selama atau setelah pemeriksaan; AI Agent mentranskrip ucapan dan menyusun draf **Berita Acara Pemeriksaan (BAP)** terstruktur secara otomatis.
* **Rujukan Tren AI 2026:** Gartner memproyeksikan bahwa 40% aplikasi *enterprise* akan mengintegrasikan *task-specific AI agents* untuk merencanakan dan mengeksekusi tugas multi-langkah dengan pengawasan manusia.

---

### 📍 PILAR 2: Triangulasi Multi-Dokumen & Multimodal Voice PANDAWA
* **Deskripsi:** Memperluas jangkauan ekstraksi VLM dari satu jenis dokumen (slip gaji) menjadi analisis silang multi-dokumen keuangan dan masukan suara interaktif.
* **Komponen Fitur:**
  * **Cross-Document VLM Matching:** Membaca dan menyandingkan secara otomatis antara *Slip Gaji Pekerja*, *Bukti Potong PPh 21*, *Laporan Payroll Perusahaan*, dan *Rekening Koran*. Jika PPh 21 mencerminkan gaji riil Rp6 Juta namun iuran JKN didaftarkan Rp3,5 Juta, sinyal diskrepansi multivariat langsung dipicu.
  * **Multimodal Voice PANDAWA:** Pekerja dapat mengirimkan pesan suara (*voice note*) di WhatsApp/PANDAWA bersama foto slip gaji. AI multimodal mentranskrip keluhan, mengoordinasikan verifikasi teks-gambar, dan menjaga kerahasiaan identitas (*Zero PII retention*).
* **Rujukan Tren AI 2026:** Integrasi Multimodal AI (teks, gambar, audio) yang menyatukan alur komunikasi alami pengguna tanpa mengorbankan keamanan data pribadi.

---

### 📍 PILAR 3: Explainable AI (XAI) & Counterfactual Analysis
* **Deskripsi:** Menyiapkan transparansi matematika dan penjelasan sebab-akibat yang dapat dipertanggungjawabkan kepada manajemen BPJS, auditor internal, maupun badan usaha yang diperiksa.
* **Komponen Fitur:**
  * **Counterfactual Explanation:** Menyajikan penjelasan sebab-akibat yang mudah dipahami: *"Perusahaan ini berisiko tinggi (Skor 84) terutama karena Sinyal C1 (Under-reporting) dan Sinyal C2 (Headcount Gap). Jika perusahaan mendaftarkan 15 pekerja tersembunyi, skor risiko akan turun menjadi 32 (Risiko Rendah)."*
  * **Feature Attribution Dashboard:** Menggunakan kontribusi fitur (*SHAP values*) untuk memastikan penetapan antrean audit bebas dari bias sektor atau lokasi geografis.
* **Rujukan Tren AI 2026:** Penerapan *Responsible AI* dan transparansi sistem keputusan berisiko tinggi untuk membangun kepercayaan (*trust*) antara regulator dan publik.

---

### 📍 PILAR 4: Generative Synthetic Fraud Simulator & Proactive Self-Correction
* **Deskripsi:** Mendorong pencegahan ketidakpatuhan mandiri oleh perusahaan (*Product-Led Compliance*) sekaligus memperkuat ketahanan model AI melalui data simulasi.
* **Komponen Fitur:**
  * **Portal HRD Proactive Self-Correction:** Saat HRD perusahaan mengunggah draf data iuran ke portal Edabu, AI memberikan simulasi risiko secara *real-time*: *"Perhatian: 40% staf terdeteksi didaftarkan tepat di batas UMK. Pola ini meningkatkan skor risiko pemeriksaan Selaras ke angka 82."* AI menyajikan rekomendasi koreksi mandiri sebelum sanksi berlaku.
  * **Synthetic Fraud Data Sandbox:** Membangkitkan dataset sintetis pola-pola kecurangan baru (misal manipulasi mutasi karyawan musiman) menggunakan GenAI untuk melatih model *Isolation Forest* dan *Rule Engine* tanpa perlu menggunakan data pribadi riil.
* **Rujukan Tren AI 2026:** Penggunaan data sintetis yang tervalidasi secara statistik andal untuk melatih model kecurangan di sektor keuangan/kesehatan yang terikat aturan privasi ketat.

---

### 📍 PILAR 5: Predictive Workload Forecasting & Dynamic Capacity Planning
* **Deskripsi:** Memprediksi lonjakan kasus dan mengoptimalkan penugasan lapangan secara dinamis sesuai siklus bisnis badan usaha.
* **Komponen Fitur:**
  * **Forecasting Beban Kerja Audit:** Menggunakan *machine learning time-series forecasting* pada *Knapsack Queue Planner* untuk memprediksi lonjakan ketidakselarasan iuran berdasarkan jadwal penggajian bulanan perusahaan.
  * **Dynamic Resource Allocation:** Rekomendasi redistribusi kuota jam kerja petugas Wasrik antar-kantor cabang berdasarkan potensi pemulihan fiskal tertinggi.
* **Rujukan Tren AI 2026:** *Predictive infrastructure & workload scaling* pada platform SaaS/Enterprise untuk efisiensi alokasi sumber daya hingga 25–40%.

---

## 📑 4. Matriks Pemetaan Inovasi SELARAS v2.0

| Pilar Inovasi | Komponen AI/ML | Output Utama | Manfaat Operasional | Standard & Referensi |
| :--- | :--- | :--- | :--- | :--- |
| **1. Agentic Wasrik Copilot** | LLM Agent + Voice-to-Text | Draf BAP & Strategi Audit | Hemat 60% waktu administrasi petugas | Gartner Task-Specific AI Agents |
| **2. Multi-Document VLM & Voice** | VLM Multimodal + Audio Transcriber | Triangulasi PPh 21 vs JKN & Laporan Suara | Deteksi kecurangan payroll tingkat lanjut | Multimodal App Experience (2026) |
| **3. Counterfactual XAI** | SHAP / Counterfactual Engine | Penjelasan Risiko & Skenario Perubahan | Akuntabilitas hukum & pencegahan bias | Responsible AI Governance |
| **4. Synthetic Data & Self-Correction** | GenAI Data Sandbox + Edabu AI | Koreksi Mandiri HRD & Model Anti-Fraud | Efisiensi pencegahan sebelum audit | Privacy-Preserving Synthetic Data |
| **5. Predictive Workload** | Time-Series ML + Knapsack | Proyeksi Beban & Rute Optimal | Maximized Fiscal Recovery per Jam Petugas | Capacity-Aware Operations |

---

## 🗺️ 5. Horizon Implementasi (Roadmap Development)

```
[ HORIZON 1: MVP & GO-LIVE (Saat Ini / Q4 2026) ]
  ├── Triangulasi 3 Titik Data (Laporan BU, VLM Slip Gaji, UMK)
  ├── Rule Engine Deterministik (PP 86/2013 & Perpres 82/2018)
  ├── Greedy Knapsack Queue Planner (Kapasitas Petugas)
  └── Bot Pelaporan Telegram & Portal Verifikasi Petugas

[ HORIZON 2: AGENTIC & PROACTIVE (Q1 - Q2 2027) ]
  ├── Agentic Wasrik Copilot (Pre-audit briefing & Voice-to-BAP)
  ├── Portal HRD Proactive Self-Correction (Simulasi Risiko Edabu)
  └── Counterfactual XAI Dashboard

[ HORIZON 3: MULTIMODAL & NATIONAL SCALE (Q3 - Q4 2027) ]
  ├── Cross-Document VLM Matching (PPh 21 vs JKN vs Rekening Koran)
  ├── Multimodal Voice PANDAWA (Laporan Rekaman Suara WhatsApp)
  └── Synthetic Fraud Data Sandbox & Predictive Workload Forecasting
```

---

## 🛡️ 6. Kepatuhan Hukum, Keamanan & Privasi Data

Sistem SELARAS v2.0 mempertahankan dan memperketat standar keamanan data:
1. **UU PDP No. 27/2022 (Pasal 27 & 28):** Menggunakan *One-Way Salted Hash* (`worker_hash`) untuk identitas pelapor. Foto slip gaji disimpan secara sementara di memori terisolasi dan dihapus permanen (*Zero Image Retention*) dalam kurun waktu $\le$10 menit.
2. **Isolasi Berkas Kasus Petugas:** Petugas Wasrik hanya menerima *Case Card* berisi bukti diskrepansi dan lokasi perusahaan tanpa pernah mengetahui identitas pekerja pelapor untuk mencegah intimidasi industrial.
3. **Audit Trail Deterministik:** Seluruh log eksekusi *Rule Engine* dan perubahan status kasus dicatat secara terenkripsi untuk kebutuhan audit internal BPJS Kesehatan.

---
*Dokumen ini disusun sebagai materi acuan resmi pengembangan sistem SELARAS pada Healthkathon 2026.*
