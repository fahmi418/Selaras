"""
Regional distribution analytics for the executive dashboard ("Sebaran Wilayah" tab).

Pure pandas/plotly: no Streamlit and no database access, so everything here is unit-testable.
Input is one row per company holding its LATEST risk score (NaN when never scored).

Chart choices follow the job of each view:
  - ranking of recovery potential  -> horizontal bars, emphasis on the top region
  - risk mix per region            -> stacked bars in fixed status colors (always with a legend)
  - region x sector hot spots      -> single-hue sequential heatmap
  - where the regions are          -> bubble map (size = gap, color = mean risk)
Never two y-axes; every figure has an equivalent table in the dashboard.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

# Risk levels, most severe first. Status colors are fixed (never themed) and always paired with a label.
LEVELS = ["Tinggi", "Sedang", "Rendah", "Belum dinilai"]
LEVEL_COLORS = {
    "Tinggi": "#d03b3b",
    "Sedang": "#ec835a",
    "Rendah": "#0ca30c",
    "Belum dinilai": "#c3c2b7",
}

INK = "#151515"
INK_MUTED = "#858585"
GRID = "#e5e7eb"
SURFACE = "#ffffff"
TEAL_DEEP = "#244d54"
TEAL_SOFT = "#6dddbd"
SEQUENTIAL = [[0.0, "#e6f7f2"], [0.5, "#2ecea0"], [1.0, TEAL_DEEP]]

# Approximate city-centre coordinates for the regions the project ships with.
REGION_COORDS = {
    "jkt-utara": (-6.138, 106.863),
    "jkt-selatan": (-6.261, 106.810),
    "kab-bekasi": (-6.300, 107.150),
    "kota-bandung": (-6.917, 107.619),
    "kota-surabaya": (-7.257, 112.752),
    "kota-semarang": (-6.967, 110.417),
    "kota-medan": (3.595, 98.672),
    "kota-denpasar": (-8.670, 115.212),
}

_FONT = {"family": "Inter Tight, sans-serif", "color": INK}


def load_thresholds(rules_path: Optional[Path] = None) -> dict:
    """Risk label thresholds from config/rules.yaml (same source as the officer portal)."""
    path = rules_path or Path(__file__).resolve().parent.parent / "config" / "rules.yaml"
    try:
        import yaml

        labels = yaml.safe_load(path.read_text(encoding="utf-8"))["risk_labels"]
        return {"high": float(labels["high"]), "medium": float(labels["medium"])}
    except Exception:
        return {"high": 70.0, "medium": 40.0}


def risk_level(risk, thresholds: dict) -> str:
    if risk is None or pd.isna(risk):
        return "Belum dinilai"
    if risk >= thresholds["high"]:
        return "Tinggi"
    if risk >= thresholds["medium"]:
        return "Sedang"
    return "Rendah"


def fmt_idr_jt(value: float) -> str:
    """Rupiah in millions with an Indonesian decimal comma, e.g. 12500000 -> 'Rp 12,5 jt'."""
    return f"Rp {value / 1_000_000:,.1f} jt".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def prepare_companies(df: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    out = df.copy()
    out["level"] = out["risk"].apply(lambda r: risk_level(r, thresholds))
    return out


def aggregate_regions(df: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """One row per region, sorted by total recovery potential (largest first)."""
    cols = ["region_id", "wilayah", "province", "entities", "scored", "avg_risk",
            *[lvl for lvl in LEVELS], "total_gap", "active_cases", "share_gap"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    d = prepare_companies(df, thresholds)
    rows = []
    for (rid, wilayah, province), g in d.groupby(["region_id", "wilayah", "province"], dropna=False):
        scored = g["risk"].dropna()
        row = {
            "region_id": rid,
            "wilayah": wilayah,
            "province": province,
            "entities": len(g),
            "scored": int(scored.count()),
            "avg_risk": round(float(scored.mean()), 1) if len(scored) else float("nan"),
            "total_gap": float(g["est_mid"].fillna(0).sum()),
            "active_cases": int(g["active_cases"].fillna(0).sum()),
        }
        for lvl in LEVELS:
            row[lvl] = int((g["level"] == lvl).sum())
        rows.append(row)

    out = pd.DataFrame(rows)
    total = out["total_gap"].sum()
    out["share_gap"] = out["total_gap"] / total if total else 0.0
    return out.sort_values(["total_gap", "avg_risk"], ascending=[False, False]).reset_index(drop=True)[cols]


def sector_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Mean risk of scored companies, regions as rows and sectors as columns (NaN = no scored company)."""
    scored = df.dropna(subset=["risk"])
    if scored.empty:
        return pd.DataFrame()
    return scored.pivot_table(index="wilayah", columns="sector", values="risk", aggfunc="mean").round(1)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _layout(fig: go.Figure, height: int, **extra) -> go.Figure:
    fig.update_layout(
        height=height,
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        font=_FONT,
        margin=dict(l=10, r=20, t=10, b=10),
        hoverlabel=dict(bgcolor=SURFACE, font=dict(family="Inter Tight, sans-serif", color=INK), bordercolor=GRID),
        **extra,
    )
    return fig


def fig_gap_ranking(reg: pd.DataFrame) -> go.Figure:
    """Horizontal bars of total monthly gap per region; the top region is emphasised, the rest recede."""
    d = reg.iloc[::-1]  # plotly draws the first category at the bottom
    colors = [TEAL_SOFT] * (len(d) - 1) + [TEAL_DEEP]
    fig = go.Figure(go.Bar(
        x=d["total_gap"] / 1_000_000,
        y=d["wilayah"],
        orientation="h",
        marker=dict(color=colors, cornerradius=4),
        text=[fmt_idr_jt(v) for v in d["total_gap"]],
        textposition="outside",
        cliponaxis=False,
        customdata=d[["province", "entities", "share_gap"]].to_numpy(),
        hovertemplate=(
            "<b>%{y}</b> (%{customdata[0]})<br>"
            "Potensi gap: %{text} / bulan<br>"
            "Porsi dari total: %{customdata[2]:.0%}<br>"
            "Entitas: %{customdata[1]}<extra></extra>"
        ),
    ))
    _layout(fig, height=max(220, 56 * len(d) + 40), showlegend=False)
    fig.update_xaxes(visible=False, range=[0, max(d["total_gap"].max() / 1_000_000 * 1.35, 1)])
    fig.update_yaxes(showgrid=False, linecolor=GRID, automargin=True)
    return fig


def fig_risk_composition(reg: pd.DataFrame) -> go.Figure:
    """Entity count per risk level in each region (stacked); legend always shown, 2px gaps between segments."""
    d = reg.iloc[::-1]
    fig = go.Figure()
    for lvl in LEVELS:
        counts = d[lvl]
        fig.add_trace(go.Bar(
            name=lvl,
            x=counts,
            y=d["wilayah"],
            orientation="h",
            marker=dict(color=LEVEL_COLORS[lvl], line=dict(color=SURFACE, width=2)),
            text=[str(c) if c else "" for c in counts],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(color="#ffffff" if lvl in ("Tinggi", "Rendah") else INK, size=12),
            hovertemplate=f"<b>%{{y}}</b><br>{lvl}: %{{x}} entitas<extra></extra>",
        ))
    _layout(
        fig,
        height=max(220, 56 * len(d) + 70),
        barmode="stack",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, traceorder="normal"),
    )
    fig.update_xaxes(title_text="Jumlah badan usaha", gridcolor=GRID, linecolor=GRID, zeroline=False, dtick=1)
    fig.update_yaxes(showgrid=False, linecolor=GRID, automargin=True)
    return fig


def fig_sector_heatmap(matrix: pd.DataFrame) -> go.Figure:
    """Region x sector mean risk, single-hue sequential; empty cells mean 'no scored company'."""
    fig = go.Figure(go.Heatmap(
        z=matrix.values,
        x=[s.upper() for s in matrix.columns],
        y=matrix.index,
        colorscale=SEQUENTIAL,
        zmin=0, zmax=100,
        xgap=2, ygap=2,
        text=matrix.round(0).astype("Int64").astype(str).replace("<NA>", "").values,
        texttemplate="%{text}",
        hovertemplate="<b>%{y}</b> · %{x}<br>Rata-rata skor risiko: %{z:.1f}<extra></extra>",
        hoverongaps=False,
        colorbar=dict(title=dict(text="Skor", side="right"), thickness=10, len=0.8, outlinewidth=0),
    ))
    _layout(fig, height=max(240, 52 * len(matrix) + 90))
    fig.update_xaxes(side="top", showgrid=False, linecolor=GRID)
    fig.update_yaxes(showgrid=False, linecolor=GRID, autorange="reversed", automargin=True)
    return fig


MAP_HEIGHT = 420


def _map_view(lat: pd.Series, lon: pd.Series, width_px: int, height_px: int) -> tuple[dict, float]:
    """Bounding-box center plus the largest zoom that fits all points with margin in BOTH dimensions."""
    lat_span = max(float(lat.max() - lat.min()), 0.3)
    lon_span = max(float(lon.max() - lon.min()), 0.3)
    # Visible degrees at zoom z on 512px tiles: px / 512 * 360 / 2**z
    zoom_lon = math.log2(width_px * 360 / (512 * lon_span * 1.5))
    zoom_lat = math.log2(height_px * 360 / (512 * lat_span * 1.7))
    zoom = max(3.0, min(zoom_lon, zoom_lat, 9.0))
    center = dict(lat=float((lat.max() + lat.min()) / 2), lon=float((lon.max() + lon.min()) / 2))
    return center, round(zoom, 2)


def fig_region_map(reg: pd.DataFrame) -> tuple[go.Figure | None, list[str]]:
    """Bubble map: size = monthly gap, color = mean risk. Returns (figure or None, regions without coordinates)."""
    d = reg.dropna(subset=["avg_risk"]).copy()
    d["lat"] = d["region_id"].map(lambda r: REGION_COORDS.get(r, (None, None))[0])
    d["lon"] = d["region_id"].map(lambda r: REGION_COORDS.get(r, (None, None))[1])
    missing = d[d["lat"].isna()]["wilayah"].tolist() + reg[reg["avg_risk"].isna()]["wilayah"].tolist()
    d = d.dropna(subset=["lat"])
    if d.empty:
        return None, missing

    # Marker area must stay readable even when a region's gap is 0
    basis = d["total_gap"].where(d["total_gap"] > 0, other=d["entities"] * 100_000)
    fig = go.Figure(go.Scattermap(
        lat=d["lat"], lon=d["lon"], mode="markers+text",
        marker=dict(
            size=basis, sizemode="area", sizeref=2.0 * basis.max() / (34.0 ** 2), sizemin=9,
            color=d["avg_risk"], colorscale=SEQUENTIAL, cmin=0, cmax=100,
            colorbar=dict(title=dict(text="Rata-rata skor", side="right"), thickness=10, len=0.7, outlinewidth=0),
            opacity=0.85,
        ),
        text=d["wilayah"], textposition="top center", textfont=dict(size=11, color=INK),
        customdata=d[["province", "entities", "total_gap", "avg_risk"]].to_numpy(),
        hovertemplate=(
            "<b>%{text}</b> (%{customdata[0]})<br>"
            "Rata-rata skor risiko: %{customdata[3]:.1f}<br>"
            "Entitas: %{customdata[1]}<br>"
            "Potensi gap: Rp %{customdata[2]:,.0f} / bulan<extra></extra>"
        ),
    ))
    center, zoom = _map_view(d["lat"], d["lon"], width_px=620, height_px=MAP_HEIGHT)
    _layout(fig, height=MAP_HEIGHT, map=dict(style="carto-positron", zoom=zoom, center=center))
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
    return fig, missing


# ---------------------------------------------------------------------------
# Plain-language headlines (computed from the data, never hard-coded)
# ---------------------------------------------------------------------------

def headline_gap(reg: pd.DataFrame) -> str:
    total = reg["total_gap"].sum()
    if reg.empty or total <= 0:
        return "Belum ada estimasi potensi gap yang dapat dibandingkan antarwilayah."
    top = reg.iloc[0]
    return f"{top['wilayah']} menyumbang {top['share_gap']:.0%} dari potensi gap {fmt_idr_jt(total)} per bulan."


def headline_risk(reg: pd.DataFrame) -> str:
    high = int(reg["Tinggi"].sum())
    scored = int(reg["scored"].sum())
    if scored == 0:
        return "Belum ada badan usaha yang dinilai risikonya."
    if high == 0:
        return f"Tidak ada badan usaha berisiko tinggi dari {scored} yang telah dinilai."
    worst = reg.sort_values(["Tinggi", "total_gap"], ascending=False).iloc[0]
    return (f"{high} dari {scored} badan usaha yang dinilai berisiko tinggi; "
            f"terbanyak di {worst['wilayah']} ({int(worst['Tinggi'])}).")
