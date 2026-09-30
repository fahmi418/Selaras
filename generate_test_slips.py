import os
from PIL import Image, ImageDraw, ImageFont

os.makedirs("test_slips", exist_ok=True)

def create_payslip(filename, company_name, period, employee_name, earnings, deductions, notes=""):
    # Create image canvas (800 x 1000)
    width, height = 800, 1000
    image = Image.new("RGB", (width, height), color="#FFFFFF")
    draw = ImageDraw.Draw(image)

    # Use default bitmap font or load standard font if available
    try:
        font_title = ImageFont.truetype("arial.ttf", 24)
        font_header = ImageFont.truetype("arial.ttf", 16)
        font_bold = ImageFont.truetype("arialbd.ttf", 15)
        font_regular = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font_title = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_bold = ImageFont.load_default()
        font_regular = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Draw border
    draw.rectangle([(20, 20), (width - 20, height - 20)], outline="#2C3E50", width=2)

    # Header section
    draw.text((40, 40), company_name.upper(), fill="#1A365D", font=font_title)
    draw.text((40, 75), "SLIP GAJI KARYAWAN (PAYSLIP)", fill="#4A5568", font=font_header)
    draw.text((40, 100), f"Periode: {period}", fill="#718096", font=font_regular)

    draw.line([(40, 130), (width - 40, 130)], fill="#CBD5E0", width=2)

    # Employee info
    draw.text((40, 145), f"Nama Karyawan : {employee_name}", fill="#2D3748", font=font_regular)
    draw.text((40, 170), "Departemen    : Operasional & Logistik", fill="#2D3748", font=font_regular)
    draw.text((450, 145), "Status : Karyawan Tetap", fill="#2D3748", font=font_regular)
    draw.text((450, 170), "Mata Uang : IDR (Rupiah)", fill="#2D3748", font=font_regular)

    draw.line([(40, 205), (width - 40, 205)], fill="#CBD5E0", width=1)

    # Earnings Column (Left)
    y_pos = 225
    draw.text((40, y_pos), "A. PENGHASILAN (EARNINGS)", fill="#1A365D", font=font_bold)
    y_pos += 30

    total_earnings = 0
    for label, amount in earnings:
        total_earnings += amount
        draw.text((40, y_pos), label, fill="#2D3748", font=font_regular)
        draw.text((260, y_pos), f"Rp {amount:,.0f}".replace(",", "."), fill="#2D3748", font=font_regular)
        y_pos += 26

    draw.line([(40, y_pos + 5), (350, y_pos + 5)], fill="#E2E8F0", width=1)
    y_pos += 15
    draw.text((40, y_pos), "Total Penghasilan Kotor", fill="#1A365D", font=font_bold)
    draw.text((260, y_pos), f"Rp {total_earnings:,.0f}".replace(",", "."), fill="#1A365D", font=font_bold)

    # Deductions Column (Right)
    y_ded = 225
    draw.text((450, y_ded), "B. POTONGAN (DEDUCTIONS)", fill="#9B2C2C", font=font_bold)
    y_ded += 30

    total_deductions = 0
    for label, amount in deductions:
        total_deductions += amount
        draw.text((450, y_ded), label, fill="#2D3748", font=font_regular)
        draw.text((650, y_ded), f"Rp {amount:,.0f}".replace(",", "."), fill="#2D3748", font=font_regular)
        y_ded += 26

    draw.line([(450, y_ded + 5), (width - 40, y_ded + 5)], fill="#E2E8F0", width=1)
    y_ded += 15
    draw.text((450, y_ded), "Total Potongan", fill="#9B2C2C", font=font_bold)
    draw.text((650, y_ded), f"Rp {total_deductions:,.0f}".replace(",", "."), fill="#9B2C2C", font=font_bold)

    # Net Total Section
    max_y = max(y_pos, y_ded) + 50
    draw.rectangle([(40, max_y), (width - 40, max_y + 60)], fill="#EDF2F7", outline="#CBD5E0")
    draw.text((60, max_y + 18), "GAJI BERSIH DITERIMA (TAKE HOME PAY):", fill="#1A202C", font=font_bold)
    net_salary = total_earnings - total_deductions
    draw.text((540, max_y + 16), f"Rp {net_salary:,.0f}".replace(",", "."), fill="#2B6CB0", font=font_title)

    # Footer note & signature
    draw.line([(40, height - 160), (width - 40, height - 160)], fill="#E2E8F0", width=1)
    draw.text((40, height - 140), "Catatan / Keterangan:", fill="#718096", font=font_bold)
    draw.text((40, height - 120), notes or "Dokumen ini dicetak otomatis dan sah tanpa tanda tangan basah.", fill="#718096", font=font_small)

    draw.text((560, height - 140), "Dibuat Oleh: HRD & Payroll", fill="#718096", font=font_small)
    draw.text((560, height - 70), "PT Cipta Logistik Nusantara", fill="#2D3748", font=font_bold)

    image.save(filename, "JPEG", quality=95)
    print(f"Generated test slip: {filename}")


# 1. Slip Modus #2: Under-reporting Upah (Gaji Riil 5.6 Juta, tapi BPJS dipotong hanya Rp38.000)
create_payslip(
    "test_slips/slip_modus_2_underreporting.jpg",
    company_name="PT Cipta Logistik Nusantara",
    period="September 2024",
    employee_name="Bambang Wijaya",
    earnings=[
        ("Gaji Pokok", 5_000_000),
        ("Tunjangan Jabatan", 600_000),
        ("Uang Makan Harian", 400_000),
    ],
    deductions=[
        ("BPJS Kesehatan (1%)", 38_000),  # Under-reported deduction
        ("BPJS Ketenagakerjaan", 120_000),
        ("PPh 21", 75_000),
    ],
    notes="Indikasi Modus #2: Potongan BPJS Kesehatan hanya Rp38.000 (basis upah Rp3.800.000 vs riil Rp5.600.000)"
)

# 2. Slip Selaras: Normal / OK (Gaji Pokok 5jt + Tunjangan 600rb = Basis 5.6jt -> BPJS Rp56.000)
create_payslip(
    "test_slips/slip_normal_ok.jpg",
    company_name="PT Cipta Logistik Nusantara",
    period="September 2024",
    employee_name="Ahmad Pratama",
    earnings=[
        ("Gaji Pokok", 5_000_000),
        ("Tunjangan Jabatan", 600_000),
        ("Uang Makan Harian", 400_000),
    ],
    deductions=[
        ("BPJS Kesehatan (1%)", 56_000),  # Exact 1% of 5,600,000
        ("BPJS Ketenagakerjaan", 168_000),
        ("PPh 21", 75_000),
    ],
    notes="Skenario Selaras: Potongan BPJS Kesehatan 1% tepat sesuai akumulasi upah tetap (Rp5.600.000)"
)

# 3. Slip Modus #1: Potongan Missing / Tidak Ada BPJS
create_payslip(
    "test_slips/slip_modus_missing_deduction.jpg",
    company_name="PT Mitra Usaha Makmur",
    period="September 2024",
    employee_name="Dedi Suryana",
    earnings=[
        ("Gaji Pokok", 4_500_000),
        ("Tunjangan Kehadiran", 500_000),
    ],
    deductions=[
        ("Kas Koperasi", 50_000),
        ("Potongan Keterlambatan", 25_000),
    ],
    notes="Indikasi Modus #1/Missing: Tidak ditemukan adanya potongan BPJS Kesehatan pada slip"
)
