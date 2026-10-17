"""Regional analytics behind the dashboard's 'Sebaran Wilayah' tab."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dashboard import regional

TH = {"high": 70.0, "medium": 40.0}


def _companies():
    rows = [
        # region_id, wilayah, province, sector, risk, est_mid, active_cases
        ("jkt-utara", "Jakarta Utara", "DKI Jakarta", "logistik", 82.0, 10_000_000, 1),
        ("jkt-utara", "Jakarta Utara", "DKI Jakarta", "jasa", 45.0, 4_000_000, 1),
        ("jkt-utara", "Jakarta Utara", "DKI Jakarta", "jasa", np.nan, np.nan, 0),
        ("kab-bekasi", "Kab. Bekasi", "Jawa Barat", "manufaktur", 20.0, 1_000_000, 0),
        ("xx-baru", "Kota Baru", "Provinsi X", "jasa", 75.0, 5_000_000, 1),  # no coordinates known
    ]
    return pd.DataFrame(rows, columns=["region_id", "wilayah", "province", "sector", "risk", "est_mid", "active_cases"])


def test_risk_level_uses_thresholds_and_flags_unscored():
    assert regional.risk_level(70, TH) == "Tinggi"
    assert regional.risk_level(69.9, TH) == "Sedang"
    assert regional.risk_level(40, TH) == "Sedang"
    assert regional.risk_level(39, TH) == "Rendah"
    assert regional.risk_level(float("nan"), TH) == "Belum dinilai"
    assert regional.risk_level(None, TH) == "Belum dinilai"


def test_thresholds_come_from_rules_yaml():
    from app.config import get_rules

    labels = get_rules().risk_labels
    assert regional.load_thresholds() == {"high": float(labels["high"]), "medium": float(labels["medium"])}


def test_aggregate_regions_counts_gap_and_ranks_by_gap():
    reg = regional.aggregate_regions(_companies(), TH)

    assert list(reg["wilayah"]) == ["Jakarta Utara", "Kota Baru", "Kab. Bekasi"]  # by total gap, desc
    jkt = reg.iloc[0]
    assert jkt["entities"] == 3 and jkt["scored"] == 2
    assert jkt["avg_risk"] == 63.5  # unscored company is NOT counted as 0
    assert (jkt["Tinggi"], jkt["Sedang"], jkt["Rendah"], jkt["Belum dinilai"]) == (1, 1, 0, 1)
    assert jkt["total_gap"] == 14_000_000 and jkt["active_cases"] == 2
    assert math.isclose(reg["share_gap"].sum(), 1.0)
    assert math.isclose(jkt["share_gap"], 14 / 20)


def test_aggregate_handles_empty_input():
    empty = pd.DataFrame(columns=["region_id", "wilayah", "province", "sector", "risk", "est_mid", "active_cases"])
    reg = regional.aggregate_regions(empty, TH)
    assert reg.empty
    assert "wilayah" in reg.columns


def test_sector_matrix_ignores_unscored_companies():
    m = regional.sector_matrix(_companies())
    assert m.loc["Jakarta Utara", "jasa"] == 45.0  # the NaN-risk jasa company is excluded from the mean
    assert pd.isna(m.loc["Kab. Bekasi", "jasa"])
    assert regional.sector_matrix(_companies().assign(risk=np.nan)).empty


def test_all_figures_build_from_data():
    reg = regional.aggregate_regions(_companies(), TH)

    ranking = regional.fig_gap_ranking(reg)
    assert list(ranking.data[0].y)[-1] == "Jakarta Utara"  # largest drawn on top

    mix = regional.fig_risk_composition(reg)
    assert [t.name for t in mix.data] == regional.LEVELS  # legend entries for every level, fixed order
    assert mix.layout.barmode == "stack"

    heat = regional.fig_sector_heatmap(regional.sector_matrix(_companies()))
    assert heat.data[0].zmin == 0 and heat.data[0].zmax == 100

    fig, missing = regional.fig_region_map(reg)
    assert fig is not None
    assert "Kota Baru" in missing  # scored but no coordinates -> reported, not silently dropped
    assert set(fig.data[0].text) == {"Jakarta Utara", "Kab. Bekasi"}


def test_map_view_fits_every_point_in_both_dimensions():
    coords = [regional.REGION_COORDS[r] for r in ("kota-medan", "kota-denpasar", "jkt-utara")]
    lat, lon = pd.Series([c[0] for c in coords]), pd.Series([c[1] for c in coords])
    center, zoom = regional._map_view(lat, lon, width_px=620, height_px=regional.MAP_HEIGHT)

    # Visible degrees at this zoom must cover the spread with margin (this used to cut Medan off)
    visible_lat = regional.MAP_HEIGHT / 512 * 360 / 2 ** zoom
    visible_lon = 620 / 512 * 360 / 2 ** zoom
    assert visible_lat > (lat.max() - lat.min()) and visible_lon > (lon.max() - lon.min())
    assert lat.min() < center["lat"] < lat.max() and lon.min() < center["lon"] < lon.max()

    _, single_zoom = regional._map_view(lat.iloc[:1], lon.iloc[:1], 620, regional.MAP_HEIGHT)
    assert 3 <= single_zoom <= 9


def test_map_reports_regions_without_scores():
    df = _companies().assign(risk=np.nan, est_mid=np.nan)
    fig, missing = regional.fig_region_map(regional.aggregate_regions(df, TH))
    assert fig is None
    assert set(missing) == {"Jakarta Utara", "Kab. Bekasi", "Kota Baru"}


def test_headlines_are_computed_from_data():
    reg = regional.aggregate_regions(_companies(), TH)
    assert "Jakarta Utara" in regional.headline_gap(reg) and "70%" in regional.headline_gap(reg)
    assert "2 dari 4" in regional.headline_risk(reg)

    none_high = regional.aggregate_regions(_companies().assign(risk=10.0), TH)
    assert "Tidak ada" in regional.headline_risk(none_high)


def test_fmt_idr_jt_uses_indonesian_separators():
    assert regional.fmt_idr_jt(12_500_000) == "Rp 12,5 jt"
    assert regional.fmt_idr_jt(1_234_500_000) == "Rp 1.234,5 jt"


async def test_dashboard_renders_regional_tab_without_errors(seeded_db):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py"), default_timeout=90).run()
    assert not at.exception, [e.value for e in at.exception]

    html = " ".join(m.value for m in at.markdown)
    assert "Wilayah Terpantau" in html and "Total Potensi Gap" in html
    assert any("Peringkat potensi pemulihan iuran" in m.value for m in at.markdown)
