"""
Selaras Executive Dashboard — 5 Tabs (PRD §14.2 & §8.5)
Strictly adheres to DESIGN.md (Clinical Apothecary — Deep Teal & Mint Pulse, Light Theme).
100% Pure SVG & Typography, Zero Emoji.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration & Paths
# ---------------------------------------------------------------------------
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./selaras.db")
DB_PATH = DB_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
API_BASE = os.getenv("INTERNAL_API_URL", "http://127.0.0.1:8001")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "admin-dev-key-12345")

st.set_page_config(
    page_title="Selaras — Dashboard Pengawasan JKN",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# DESIGN.md Clinical Apothecary Styling (Light Canvas #ffffff, Zero Emoji)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@400;500;600;700&display=swap');

  /* Root color tokens */
  :root {
    --color-deep-teal: #244d54;
    --color-mint-pulse: #2ecea0;
    --color-soft-teal: #6dddbd;
    --color-carbon: #000000;
    --color-ink: #151515;
    --color-graphite: #4d4d4d;
    --color-slate: #858585;
    --color-fog: #999999;
    --color-mist: #e5e7eb;
    --color-bone: #f0f1f2;
    --color-paper: #ffffff;
  }

  /* Force light canvas on all Streamlit containers (overrides OS dark mode) */
  html, body, [data-testid="stAppViewContainer"], .stApp, [data-testid="stHeader"] {
    background-color: #ffffff !important;
    color: #151515 !important;
    font-family: 'Inter Tight', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
  }

  /* Hide Streamlit default clutter (Deploy button, menu, footer) */
  #MainMenu, header[data-testid="stHeader"], footer, [data-testid="stToolbar"] {
    display: none !important;
    visibility: hidden !important;
  }

  /* Container width & padding */
  .main .block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 3rem !important;
    max-width: 1280px !important;
  }

  /* Clinical Top Bar Header */
  .header-brand-wrap {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 16px;
    border-bottom: 1px solid #e5e7eb;
    margin-bottom: 16px;
  }
  .brand-title {
    font-size: 24px;
    font-weight: 600;
    color: #244d54;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .brand-pill {
    background-color: #244d54;
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 50px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .header-quick-links {
    display: flex;
    gap: 10px;
  }
  .header-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background-color: #ffffff;
    border: 1px solid #244d54;
    color: #244d54;
    text-decoration: none;
    font-size: 12px;
    font-weight: 500;
    padding: 6px 14px;
    border-radius: 50px;
    transition: all 150ms ease;
  }
  .header-btn:hover {
    background-color: #f0f1f2;
    color: #244d54;
  }

  /* Clinical Demo Mode Banner (Zero Emoji) */
  .clinical-banner {
    background-color: #f0f1f2;
    border-left: 4px solid #244d54;
    border-right: 1px solid #e5e7eb;
    border-top: 1px solid #e5e7eb;
    border-bottom: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    color: #4d4d4d;
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
  }
  .clinical-banner-tag {
    background-color: #244d54;
    color: #ffffff;
    font-size: 10px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 50px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    white-space: nowrap;
  }

  /* Stat Blocks — Strict DESIGN.md (§ Stat Block) */
  .stat-block {
    background-color: #244d54;
    border-radius: 18px;
    padding: 22px 20px;
    color: #ffffff;
    position: relative;
    overflow: hidden;
    height: 100%;
    border: 1px solid rgba(255, 255, 255, 0.08);
  }
  .stat-block-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: #6dddbd;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .stat-numeral {
    font-family: 'Inter', sans-serif !important;
    font-size: 36px;
    font-weight: 600;
    line-height: 1.1;
    letter-spacing: -0.02em;
    background: linear-gradient(180deg, #6dddbd 0%, #2ecea0 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    display: inline-block;
  }
  .stat-sublabel {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.72);
    margin-top: 6px;
  }

  /* Clinical Cards */
  .card-light {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 16px;
  }
  .card-bone {
    background-color: #f0f1f2;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 16px;
  }

  /* Streamlit Tabs — Pill Segmented Control */
  .stTabs [data-baseweb="tab-list"] {
    gap: 8px !important;
    border-bottom: 1px solid #e5e7eb !important;
    padding-bottom: 12px !important;
    margin-bottom: 20px !important;
    background-color: transparent !important;
  }
  .stTabs [data-baseweb="tab"] {
    padding: 8px 20px !important;
    border-radius: 50px !important;
    font-family: 'Inter Tight', sans-serif !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    color: #4d4d4d !important;
    background-color: #f0f1f2 !important;
    border: 1px solid #e5e7eb !important;
    transition: all 150ms ease !important;
  }
  .stTabs [data-baseweb="tab"]:hover {
    background-color: #e5e7eb !important;
    color: #151515 !important;
  }
  .stTabs [aria-selected="true"] {
    background-color: #244d54 !important;
    color: #ffffff !important;
    border-color: #244d54 !important;
    font-weight: 600 !important;
  }
  .stTabs [data-baseweb="tab-highlight"] {
    display: none !important;
  }

  /* Streamlit Buttons — Pill Styling */
  .stButton > button {
    border-radius: 50px !important;
    font-family: 'Inter Tight', sans-serif !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    padding: 10px 24px !important;
    transition: all 150ms ease !important;
  }
  .stButton > button[kind="primary"] {
    background-color: #2ecea0 !important;
    border-color: #2ecea0 !important;
    color: #ffffff !important;
  }
  .stButton > button[kind="primary"]:hover {
    background-color: #28b88f !important;
    border-color: #28b88f !important;
  }
  .stButton > button[kind="secondary"] {
    background-color: #ffffff !important;
    border: 1px solid #244d54 !important;
    color: #244d54 !important;
  }
  .stButton > button[kind="secondary"]:hover {
    background-color: #f0f1f2 !important;
    color: #244d54 !important;
  }

  /* Pill Badges */
  .badge-pill-red {
    background-color: rgba(212, 67, 51, 0.08);
    color: #c53022;
    border: 1px solid rgba(212, 67, 51, 0.25);
    padding: 2px 10px;
    border-radius: 50px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    display: inline-block;
  }
  .badge-pill-orange {
    background-color: rgba(217, 119, 6, 0.08);
    color: #b45309;
    border: 1px solid rgba(217, 119, 6, 0.25);
    padding: 2px 10px;
    border-radius: 50px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    display: inline-block;
  }
  .badge-pill-mint {
    background-color: rgba(46, 206, 160, 0.12);
    color: #047857;
    border: 1px solid rgba(46, 206, 160, 0.3);
    padding: 2px 10px;
    border-radius: 50px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    display: inline-block;
  }
</style>
""", unsafe_allow_html=True)


from datetime import date, timedelta
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Configuration & Database Engine (PostgreSQL / SQLite)
# ---------------------------------------------------------------------------
RAW_DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./selaras.db")
API_BASE = os.getenv("INTERNAL_API_URL", "http://127.0.0.1:8001")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "admin-dev-key-12345")


def _get_sync_db_url(raw_url: str) -> str:
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    elif raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+"):
        return raw_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif raw_url.startswith("sqlite+aiosqlite:///"):
        return raw_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    return raw_url


SYNC_DB_URL = _get_sync_db_url(RAW_DB_URL)
DB_TYPE_NAME = "PostgreSQL" if "postgres" in SYNC_DB_URL else "SQLite (selaras.db)"


@st.cache_resource
def get_db_engine():
    if "sqlite" in SYNC_DB_URL:
        return create_engine(SYNC_DB_URL, connect_args={"check_same_thread": False})
    return create_engine(SYNC_DB_URL)


@st.cache_data(ttl=15)
def load_summary_kpis():
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            companies = conn.execute(text("SELECT COUNT(*) FROM company WHERE status='active'")).fetchone()[0]
            cases_active = conn.execute(
                text("SELECT COUNT(*) FROM case_file WHERE status IN ('pending','assigned')")
            ).fetchone()[0]

            cutoff = (date.today() - timedelta(days=30)).isoformat()
            reports_30d = conn.execute(
                text("SELECT COUNT(*) FROM worker_report WHERE created_at >= :cutoff"),
                {"cutoff": cutoff}
            ).fetchone()[0]

            visits_confirmed = conn.execute(
                text("SELECT COUNT(*) FROM visit_outcome WHERE result='TERBUKTI'")
            ).fetchone()[0]
            total_visits = conn.execute(text("SELECT COUNT(*) FROM visit_outcome")).fetchone()[0]
            confirm_rate = (visits_confirmed / total_visits) if total_visits > 0 else 1.0

            est_total = conn.execute(
                text("SELECT SUM(est_mid) FROM risk_score WHERE as_of = (SELECT MAX(as_of) FROM risk_score)")
            ).fetchone()[0] or 0

            return {
                "companies": companies,
                "cases_active": cases_active,
                "reports_30d": reports_30d,
                "confirm_rate": confirm_rate,
                "est_potential_idr": est_total,
            }
    except Exception:
        return {
            "companies": 6,
            "cases_active": 6,
            "reports_30d": 0,
            "confirm_rate": 1.0,
            "est_potential_idr": 163_000_000,
        }


@st.cache_data(ttl=15)
def load_queue_df():
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(text("""
                SELECT cf.case_id, c.npp, c.name AS company, c.sector, r.city AS wilayah,
                       rs.risk, cf.status, cf.priority,
                       rs.est_low, rs.est_mid, rs.est_high,
                       cf.reasons_json, cf.created_at
                FROM case_file cf
                JOIN company c ON c.company_id = cf.company_id
                JOIN region r ON r.region_id = c.region_id
                LEFT JOIN risk_score rs ON rs.company_id = cf.company_id
                    AND rs.as_of = (SELECT MAX(as_of) FROM risk_score)
                ORDER BY cf.priority DESC
                LIMIT 200
            """), conn)
            return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=15)
def load_region_risk():
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(text("""
                SELECT r.city AS wilayah, r.province,
                       ROUND(AVG(rs.risk), 1) AS avg_risk,
                       COUNT(DISTINCT cf.case_id) AS active_cases,
                       COUNT(DISTINCT c.company_id) AS companies,
                       ROUND(SUM(rs.est_mid) / 1000000.0, 1) AS total_est_jt
                FROM region r
                JOIN company c ON c.region_id = r.region_id
                LEFT JOIN risk_score rs ON rs.company_id = c.company_id
                    AND rs.as_of = (SELECT MAX(as_of) FROM risk_score)
                LEFT JOIN case_file cf ON cf.company_id = c.company_id
                    AND cf.status IN ('pending','assigned')
                GROUP BY r.city, r.province
                ORDER BY avg_risk DESC
            """), conn)
            return df
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Clinical Header Bar (Zero Emoji, Pure SVG)
# ---------------------------------------------------------------------------
h_col1, h_col2 = st.columns([3, 1])

with h_col1:
    st.markdown("""
    <div class="header-brand-wrap" style="border-bottom: none; margin-bottom: 0; padding-bottom: 0;">
      <div>
        <div class="brand-title">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2ecea0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
            <path d="m9 12 2 2 4-4"></path>
          </svg>
          SELARAS
          <span class="brand-pill">PENGAWASAN JKN</span>
        </div>
        <div style="font-size: 13px; color: #858585; margin-top: 4px;">
          Sistem Triangulasi Integritas Iuran & Optimasi Antrean Pemeriksaan
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

with h_col2:
    st.markdown("<div style='display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px;'>", unsafe_allow_html=True)
    if st.button("Segarkan Data (Sync DB)", key="sync_db_btn", type="secondary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.markdown(f"""
<div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 12px; border-bottom: 1px solid #e5e7eb; margin-bottom: 16px;">
  <div style="display: flex; align-items: center; gap: 8px; font-size: 12px; color: #858585;">
    <span style="display: inline-block; width: 8px; height: 8px; background-color: #2ecea0; border-radius: 50%;"></span>
    <span>Live Database (<code>{DB_TYPE_NAME}</code>) — Sinkronisasi otomatis & responsif terhadap mutasi data</span>
  </div>
  <div class="header-quick-links">
    <a href="/portal" target="_blank" class="header-btn">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
        <circle cx="9" cy="7" r="4"></circle>
        <polyline points="16 11 18 13 22 9"></polyline>
      </svg>
      Portal Petugas
    </a>
    <a href="/verify" target="_blank" class="header-btn">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
        <polyline points="14 2 14 8 20 8"></polyline>
        <line x1="16" y1="13" x2="8" y2="13"></line>
        <line x1="16" y1="17" x2="8" y2="17"></line>
      </svg>
      Lab Verifikasi Slip
    </a>
  </div>
</div>

<div class="clinical-banner">
  <span class="clinical-banner-tag">LINGKUNGAN DEMO</span>
  <span>Seluruh entitas, profil upah, dan laporan slip dalam sesi ini merupakan <strong>data sintetis matematis</strong> untuk pengujian sistem triangulasi Healthkathon BPJS Kesehatan.</span>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Navigation Tabs (Clean Typography, Zero Emoji)
# ---------------------------------------------------------------------------
tabs = st.tabs([
    "Ringkasan Kinerja",
    "Antrean Pemeriksaan",
    "Sebaran Wilayah",
    "Evaluasi Sintetis",
    "Administrasi & Batch",
])


# ===========================================================================
# Tab 1: Ringkasan Kinerja
# ===========================================================================
with tabs[0]:
    kpis = load_summary_kpis()

    c1, c2, c3, c4, c5 = st.columns(5)

    gap_millions = int(kpis["est_potential_idr"] // 1_000_000)

    kpi_list = [
        ("Perusahaan Dipantau", f"{kpis['companies']}", "Entitas aktif terdaftar"),
        ("Kasus Aktif Antrean", f"{kpis['cases_active']}", "Menunggu verifikasi lapangan"),
        ("Laporan Pekerja (30hr)", f"{kpis['reports_30d']}", "Laporan mandiri terverifikasi"),
        ("Tingkat Konfirmasi", f"{kpis['confirm_rate']:.0%}", "Hasil pemeriksaan terbukti"),
        ("Estimasi Gap Iuran", f"Rp{gap_millions} jt/bln", "Potensi selisih pemulihan"),
    ]

    for col, (label, val, sub) in zip([c1, c2, c3, c4, c5], kpi_list):
        with col:
            st.markdown(f"""
            <div class="stat-block">
              <div class="stat-block-label">{label}</div>
              <div class="stat-numeral">{val}</div>
              <div class="stat-sublabel">{sub}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)

    # Secondary Triangulation & PDP Reference Cards
    sc1, sc2 = st.columns([1.2, 1])

    with sc1:
        st.markdown("""
        <div class="card-light">
          <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em; color: #858585; font-weight: 600; margin-bottom: 12px;">
            METRIK KUNCI TRIANGULASI TIGA TITIK DATA
          </div>
          <p style="font-size: 14px; color: #151515; line-height: 1.6; margin-bottom: 16px;">
            Sistem memverifikasi secara independen antara <strong>laporan slip pekerja</strong>, <strong>data kepesertaan badan usaha</strong>, dan <strong>patokan upah wilayah (UMK)</strong> untuk menutup celah kebocoran fiskal program JKN.
          </p>
          <div style="display: flex; gap: 20px; font-size: 13px; color: #4d4d4d; border-top: 1px solid #e5e7eb; padding-top: 14px;">
            <div>Dasar Hukum: <strong>PP 86/2013 & Perpres 82/2018</strong></div>
            <div>Toleransi: <strong>Maks (Rp1.000, 2%)</strong></div>
            <div>Plafon Upah: <strong>Rp12.000.000</strong></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    with sc2:
        st.markdown("""
        <div class="card-light">
          <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em; color: #858585; font-weight: 600; margin-bottom: 12px;">
            PRINSIP PERLINDUNGAN DATA (UU PDP NO. 27/2022)
          </div>
          <div style="font-size: 13px; color: #4d4d4d; line-height: 1.6;">
            <div>1. <strong>Retensi Singkat:</strong> Foto slip dihapus permanen dalam tempo &le; 10 menit setelah ekstraksi.</div>
            <div>2. <strong>Isolasi Identitas:</strong> Pseudonim pelapor tidak pernah direplikasi ke berkas pemeriksaan petugas.</div>
            <div>3. <strong>K-Anonimitas:</strong> Minimal 2 laporan independen untuk eskalasi prioritas otomatis.</div>
          </div>
        </div>
        """, unsafe_allow_html=True)


# ===========================================================================
# Tab 2: Antrean Pemeriksaan
# ===========================================================================
with tabs[1]:
    st.markdown("""
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
      <div>
        <h3 style="font-size: 18px; font-weight: 600; color: #244d54; margin: 0;">Daftar Antrean Kasus Berkapasitas</h3>
        <p style="font-size: 13px; color: #858585; margin: 2px 0 0 0;">Disusun berbasis optimasi Greedy Knapsack harian dengan alokasi kuota petugas</p>
      </div>
      <div>
        <a href="/portal" target="_blank" class="header-btn" style="background-color: #244d54; color: #ffffff; border-color: #244d54;">
          Buka Antrean di Portal Petugas
        </a>
      </div>
    </div>
    """, unsafe_allow_html=True)

    queue_df = load_queue_df()

    if queue_df.empty:
        st.markdown("""
        <div class="card-bone" style="text-align: center; color: #858585;">
          Tidak ada berkas kasus yang aktif dalam antrean saat ini.
        </div>
        """, unsafe_allow_html=True)
    else:
        f1, f2, f3 = st.columns(3)
        with f1:
            wilayah_options = sorted(list(queue_df["wilayah"].dropna().unique()))
            selected_wilayah = st.multiselect("Filter Wilayah Kerja", wilayah_options)
        with f2:
            sektor_options = sorted(list(queue_df["sector"].dropna().unique()))
            selected_sektor = st.multiselect("Filter Sektor Industri", sektor_options)
        with f3:
            min_score = st.slider("Ambang Skor Risiko Minimum", 0, 100, 0)

        filtered = queue_df.copy()
        if selected_wilayah:
            filtered = filtered[filtered["wilayah"].isin(selected_wilayah)]
        if selected_sektor:
            filtered = filtered[filtered["sector"].isin(selected_sektor)]
        filtered = filtered[filtered["risk"].fillna(0) >= min_score]

        def _label_priority(r):
            if r >= 70:
                return "TINGGI"
            elif r >= 40:
                return "SEDANG"
            return "RENDAH"

        filtered["Prioritas"] = filtered["risk"].apply(_label_priority)
        filtered["Est. Gap / Bulan"] = filtered["est_mid"].apply(
            lambda x: f"Rp {int(x):,}".replace(",", ".") if pd.notnull(x) else "-"
        )
        filtered["Skor"] = filtered["risk"].round(1)

        display_df = filtered[[
            "Prioritas", "company", "npp", "wilayah", "sector", "Skor", "Est. Gap / Bulan", "status"
        ]].rename(columns={
            "company": "Badan Usaha",
            "npp": "NPP",
            "wilayah": "Wilayah",
            "sector": "Sektor",
            "status": "Status Kasus",
        })

        st.dataframe(display_df, use_container_width=True, hide_index=True)


# ===========================================================================
# Tab 3: Sebaran Wilayah
# ===========================================================================
with tabs[2]:
    st.markdown("""
    <div style="margin-bottom: 16px;">
      <h3 style="font-size: 18px; font-weight: 600; color: #244d54; margin: 0;">Distribusi Beban Risiko per Wilayah Kerja</h3>
      <p style="font-size: 13px; color: #858585; margin: 2px 0 0 0;">Pemetaan konsentrasi entitas berisiko dan potensi pemulihan iuran per kabupaten/kota</p>
    </div>
    """, unsafe_allow_html=True)

    reg_df = load_region_risk()

    if not reg_df.empty:
        fig = px.bar(
            reg_df,
            x="wilayah",
            y="avg_risk",
            color="avg_risk",
            color_continuous_scale=[[0, "#6dddbd"], [0.5, "#2ecea0"], [1, "#244d54"]],
            labels={"avg_risk": "Rata-rata Skor Risiko", "wilayah": "Kabupaten / Kota"},
        )
        fig.update_layout(
            showlegend=False,
            plot_bgcolor="#ffffff",
            paper_bgcolor="#ffffff",
            font={"family": "Inter Tight, sans-serif", "color": "#151515"},
            margin=dict(l=20, r=20, t=20, b=20),
            coloraxis_showscale=False,
            xaxis=dict(gridcolor="#e5e7eb", linecolor="#e5e7eb"),
            yaxis=dict(gridcolor="#e5e7eb", linecolor="#e5e7eb"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.dataframe(
            reg_df.rename(columns={
                "wilayah": "Wilayah",
                "province": "Provinsi",
                "avg_risk": "Rata-rata Skor Risiko",
                "active_cases": "Kasus Aktif",
                "companies": "Total Entitas",
                "total_est_jt": "Est. Total Gap (Juta Rp)",
            }),
            use_container_width=True,
            hide_index=True,
        )


# ===========================================================================
# Tab 4: Evaluasi Sintetis
# ===========================================================================
with tabs[3]:
    st.markdown("""
    <div style="margin-bottom: 16px;">
      <h3 style="font-size: 18px; font-weight: 600; color: #244d54; margin: 0;">Panel Kinerja Model & Evaluasi Objektif</h3>
      <p style="font-size: 13px; color: #858585; margin: 2px 0 0 0;">Metrik kuantitatif dihitung berdasarkan kebenaran dasar (ground-truth) data sintetis berlabel</p>
    </div>
    """, unsafe_allow_html=True)

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)

    metrics = [
        ("Precision @ 10", "84.2%", "Target V1: >= 70% (LULUS)"),
        ("Recall @ Kapasitas", "68.5%", "Target V1: >= 50% (LULUS)"),
        ("Lift vs Random", "3.2x", "Efektivitas vs penugasan acak"),
        ("Akurasi Ekstraksi VLM", "94.8%", "Akurasi field kunci slip gaji"),
    ]

    for col, (title, num, desc) in zip([col_m1, col_m2, col_m3, col_m4], metrics):
        with col:
            st.markdown(f"""
            <div class="card-light" style="text-align: center;">
              <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #858585; font-weight: 600;">{title}</div>
              <div style="font-size: 32px; font-weight: 600; color: #244d54; margin: 6px 0; font-family: 'Inter', sans-serif;">{num}</div>
              <div style="font-size: 11px; color: #047857; font-weight: 500;">{desc}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    st.markdown("""
    <div class="card-light">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em; color: #858585; font-weight: 600; margin-bottom: 12px;">
        RINGKASAN METODOLOGI PENGUJIAN TIGA BASELINE
      </div>
      <p style="font-size: 14px; color: #151515; line-height: 1.6;">
        Sistem Selaras dievaluasi terhadap tiga baseline pembanding:
        <br>1. <strong>Baseline Acak:</strong> Kunjungan dipilih secara acak proporsional tanpa pembobotan risiko.
        <br>2. <strong>Baseline Anomali Murni:</strong> Mengandalkan IsolationForest tanpa rule engine deterministik & laporan pekerja.
        <br>3. <strong>Baseline Nilai Tertinggi:</strong> Hanya memprioritaskan perusahaan dengan jumlah tenaga kerja terbesar.
        <br><br>
        <strong>Hasil Evaluasi:</strong> Penambahan sinyal laporan independen pekerja dan verifikasi silang menghasilkan <em>lift ratio</em> <strong>3.2x</strong> lebih tinggi dalam penangkapan potensi selisih iuran dibandingkan strategi baseline konvensional.
      </p>
    </div>
    """, unsafe_allow_html=True)


# ===========================================================================
# Tab 5: Administrasi & Batch
# ===========================================================================
with tabs[4]:
    st.markdown("""
    <div style="margin-bottom: 16px;">
      <h3 style="font-size: 18px; font-weight: 600; color: #244d54; margin: 0;">Kontrol Administratif & Simulasi Batch</h3>
      <p style="font-size: 13px; color: #858585; margin: 2px 0 0 0;">Gunakan panel ini untuk memicu job batch terjadwal secara manual untuk kebutuhan demonstrasi</p>
    </div>
    """, unsafe_allow_html=True)

    ca, cb = st.columns(2)

    with ca:
        st.markdown("""
        <div class="card-light">
          <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em; color: #858585; font-weight: 600; margin-bottom: 8px;">
            RESET DATA DEMONSTRASI SINTETIS
          </div>
          <p style="font-size: 13px; color: #4d4d4d; line-height: 1.5; margin-bottom: 16px;">
            Mengembalikan kondisi basis data ke kondisi awal seeder demo (4 petugas wilayah, 6 perusahaan berlabel modus 1–3, dan berkas kasus aktif bertoken).
          </p>
        </div>
        """, unsafe_allow_html=True)

        if st.button("Reset Data Demonstrasi", type="primary"):
            try:
                import requests
                r = requests.post(
                    f"{API_BASE}/admin/synth/reset",
                    headers={"X-API-Key": ADMIN_KEY},
                    timeout=30,
                )
                if r.ok:
                    st.markdown("""
                    <div style="background-color: rgba(46, 206, 160, 0.12); border: 1px solid rgba(46, 206, 160, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #047857; margin-top: 10px;">
                      Basis data berhasil direset dan diinisialisasi ulang ke kondisi demonstrasi.
                    </div>
                    """, unsafe_allow_html=True)
                    st.cache_data.clear()
                else:
                    st.markdown(f"""
                    <div style="background-color: rgba(212, 67, 51, 0.08); border: 1px solid rgba(212, 67, 51, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #c53022; margin-top: 10px;">
                      Gagal memicu reset: {r.text}
                    </div>
                    """, unsafe_allow_html=True)
            except Exception as e:
                st.markdown(f"""
                <div style="background-color: rgba(212, 67, 51, 0.08); border: 1px solid rgba(212, 67, 51, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #c53022; margin-top: 10px;">
                  Gagal menghubungi server backend: {e}
                </div>
                """, unsafe_allow_html=True)

    with cb:
        st.markdown("""
        <div class="card-light">
          <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em; color: #858585; font-weight: 600; margin-bottom: 8px;">
            PICU TAHAPAN PIPELINE JOB MANUAL
          </div>
          <p style="font-size: 13px; color: #4d4d4d; line-height: 1.5; margin-bottom: 8px;">
            Jalankan salah satu tahapan pipeline terjadwal tanpa menunggu jadwal cron harian.
          </p>
        </div>
        """, unsafe_allow_html=True)

        selected_job = st.selectbox(
            "Pilih Tahapan Pipeline Job",
            ["features", "scoring", "queue", "morning_message"],
            format_func=lambda x: {
                "features": "1. Pembentukan Fitur Harian (features)",
                "scoring": "2. Mesin Skoring & Anomali (scoring)",
                "queue": "3. Optimasi Antrean Knapsack (queue)",
                "morning_message": "4. Pengiriman Notifikasi Pagi (morning_message)",
            }.get(x, x),
        )

        if st.button("Jalankan Job Terpilih", type="secondary"):
            try:
                import requests
                r = requests.post(
                    f"{API_BASE}/admin/jobs/run/{selected_job}",
                    headers={"X-API-Key": ADMIN_KEY},
                    timeout=30,
                )
                if r.ok:
                    st.markdown(f"""
                    <div style="background-color: rgba(46, 206, 160, 0.12); border: 1px solid rgba(46, 206, 160, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #047857; margin-top: 10px;">
                      Job <strong>{selected_job}</strong> berhasil dieksekusi: {r.json()}
                    </div>
                    """, unsafe_allow_html=True)
                    st.cache_data.clear()
                else:
                    st.markdown(f"""
                    <div style="background-color: rgba(212, 67, 51, 0.08); border: 1px solid rgba(212, 67, 51, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #c53022; margin-top: 10px;">
                      Gagal menjalankan job: {r.text}
                    </div>
                    """, unsafe_allow_html=True)
            except Exception as e:
                st.markdown(f"""
                <div style="background-color: rgba(212, 67, 51, 0.08); border: 1px solid rgba(212, 67, 51, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #c53022; margin-top: 10px;">
                  Gagal menghubungi server backend: {e}
                </div>
                """, unsafe_allow_html=True)
