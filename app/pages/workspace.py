"""
Workspace page — auto-removes Reversed/Reversal journal groups,
shows a before/after preview with download, then lets the user
continue seamlessly to Book Segregation.
"""

import streamlit as st
import pandas as pd
from pathlib import Path
import sys
import re
from io import BytesIO

sys.path.append(str(Path(__file__).parent.parent))

from utils import load_logo
from constants import UI_LABELS, COLOR_CODES
from config import AppConfig


# ── Navigation helpers ────────────────────────────────────────────────────────

def go_to_home():
    """Reset state and return to home."""
    for key in ('df_original', 'deletion_queue', 'current_matches',
                'uploaded_file', 'original_filename',
                'processed_df', 'processed_file_data'):
        st.session_state.pop(key, None)
    st.session_state.deletion_queue = set()
    st.session_state.current_matches = []
    st.session_state.current_page = 'home'
    st.rerun()


def go_to_segregation():
    """Commit cleaned data to session and navigate to segregation."""
    df = st.session_state.df_original
    reversed_indices = get_reversed_indices(df)
    st.session_state.processed_df = (
        df.drop(reversed_indices, errors='ignore') if reversed_indices
        else df.copy()
    )
    st.session_state.current_page = 'segregation'
    st.rerun()


# ── Core logic ────────────────────────────────────────────────────────────────

def get_reversed_indices(df):
    """
    Return sorted list of row indices that belong to Reversed/Reversal
    journal groups (header + body + Total row + blank separator).
    Only the *group header* must match — individual lines inside a
    normal group that mention 'reversal' are left untouched.
    """
    first_col = df.columns[0]
    all_indices = df.index.tolist()
    expanded = set()

    for pos, idx in enumerate(all_indices):
        cell_val = str(df.iloc[pos][first_col]).strip()
        if (re.match(r"^ID\s+\d+", cell_val, re.IGNORECASE) and
                re.search(r"revers(ed|al)", cell_val, re.IGNORECASE)):

            group_end_pos = pos
            for fwd in range(pos, min(pos + 100, len(all_indices))):
                fwd_val = str(df.iloc[fwd][first_col]).strip().lower()
                if fwd_val == "total":
                    group_end_pos = fwd
                    if fwd + 1 < len(all_indices):
                        group_end_pos = fwd + 1
                    break

            for p in range(pos, group_end_pos + 1):
                expanded.add(all_indices[p])

    return sorted(list(expanded))


def highlight_reversed(row, reversed_set):
    if row.name in reversed_set:
        return [f'background-color: {COLOR_CODES["RED_HIGHLIGHT"]}'] * len(row)
    return [''] * len(row)


# ── Page render ───────────────────────────────────────────────────────────────

def render_workspace_page():
    # ── CSS ──────────────────────────────────────────────────────────────
    st.markdown("""
        <style>
        .nav-bar {
            background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
            padding: 1.5rem 2rem;
            border-radius: 16px;
            margin-bottom: 2rem;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }
        .nav-bar-inner {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .nav-title  { color: white !important; font-size: 1.8rem; font-weight: 700; margin: 0; }
        .nav-subtitle { color: rgba(255,255,255,0.85) !important; font-size: 1rem; margin: 0.25rem 0 0 0; }
        .logo-container { display: flex; align-items: center; gap: 1rem; }
        .logo-image { height: 50px; width: auto; }

        .section-header-red {
            background: linear-gradient(135deg, #dc2626, #ef4444);
            color: white; padding: 1rem 1.5rem; border-radius: 12px;
            font-size: 1.3rem; font-weight: 700; text-align: center;
            margin-bottom: 1.5rem;
        }
        .section-header-green {
            background: linear-gradient(135deg, #16a34a, #4ade80);
            color: white; padding: 1rem 1.5rem; border-radius: 12px;
            font-size: 1.3rem; font-weight: 700; text-align: center;
            margin-bottom: 1.5rem;
        }
        .info-box-red {
            background: #fef2f2; border-left: 5px solid #dc2626;
            padding: 1rem; border-radius: 8px; margin: 1rem 0; color: #7f1d1d;
        }
        .info-box-green {
            background: #f0fdf4; border-left: 5px solid #16a34a;
            padding: 1rem; border-radius: 8px; margin: 1rem 0; color: #14532d;
        }
        .stats-box {
            background: white; border: 2px solid #e5e7eb; border-radius: 12px;
            padding: 1.25rem; margin: 1rem 0;
        }
        .stat-row {
            display: flex; justify-content: space-between;
            padding: 0.75rem 0; border-bottom: 1px solid #f3f4f6;
        }
        .stat-row:last-child { border-bottom: none; }
        .stat-label { color: #6b7280; font-weight: 600; }
        .stat-value { color: #1f2937; font-weight: 700; }
        .legend-container {
            background: white; border: 2px solid #e5e7eb;
            border-radius: 12px; padding: 1rem; margin-top: 1rem;
        }
        .legend-title { font-weight: 700; color: #374151; margin-bottom: 0.5rem; }
        .legend-item {
            display: inline-block; padding: 4px 12px; border-radius: 6px;
            margin: 0 8px 8px 0; font-size: 0.95rem; font-weight: 500;
        }
        .stButton > button {
            border-radius: 10px; font-weight: 600; font-size: 1rem;
            padding: 0.75rem 1.5rem; transition: all 0.2s;
        }
        .stButton > button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .stDownloadButton > button {
            background: linear-gradient(135deg, #16a34a, #22c55e);
            color: white; border: none; font-size: 1.1rem; font-weight: 700; padding: 1rem;
        }
        </style>
    """, unsafe_allow_html=True)

    # ── Nav Bar with Home link ────────────────────────────────────────────
    logo_url = load_logo()
    logo_html = f'<img src="{logo_url}" class="logo-image" alt="Logo">' if logo_url else ""

    st.markdown(f"""
        <div class="nav-bar">
            <div class="nav-bar-inner">
                <div class="logo-container">
                    {logo_html}
                    <div>
                        <h2 class="nav-title">{AppConfig.APP_TITLE}</h2>
                        <p class="nav-subtitle">{UI_LABELS['WORKSPACE_SUBTITLE']}</p>
                    </div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Slim "← Home" link-style button in top-left
    col_home, col_space = st.columns([1, 6])
    with col_home:
        if st.button("← Home", use_container_width=True, key="home_btn"):
            go_to_home()

    # ── Guard ─────────────────────────────────────────────────────────────
    if st.session_state.df_original is None:
        st.error("No file loaded. Please return home and upload a file.")
        st.stop()

    df = st.session_state.df_original
    cache_key = st.session_state.get('original_filename', '__unknown__')

    if ('reversed_indices' not in st.session_state or
            st.session_state.get('_reversed_cache_key') != cache_key):
        st.session_state.reversed_indices = set(get_reversed_indices(df))
        st.session_state._reversed_cache_key = cache_key
        st.session_state.pop('processed_file_data', None)
        st.session_state.pop('processed_df', None)

    reversed_indices = st.session_state.reversed_indices
    df_cleaned = df.drop(list(reversed_indices), errors='ignore')

    # ── Two-column layout ─────────────────────────────────────────────────
    col_before, col_after = st.columns(2)

    with col_before:
        st.markdown('<div class="section-header-red">Before: Original Data</div>',
                    unsafe_allow_html=True)
        if reversed_indices:
            st.markdown(
                f'<div class="info-box-red">🗑️ {len(reversed_indices)} '
                f'"Reversed/Reversal" row{"s" if len(reversed_indices) != 1 else ""} '
                f'will be removed</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '<div class="info-box-red">No "Reversed" or "Reversal" rows found.</div>',
                unsafe_allow_html=True
            )

        try:
            styled = df.style.apply(
                lambda row: highlight_reversed(row, reversed_indices), axis=1
            )
            st.dataframe(styled, height=AppConfig.DATAFRAME_HEIGHT, use_container_width=True)
        except Exception:
            st.dataframe(df, height=AppConfig.DATAFRAME_HEIGHT, use_container_width=True)

        st.markdown(f"""
            <div class="legend-container">
                <div class="legend-title">Color Guide:</div>
                <span class="legend-item"
                      style='background:{COLOR_CODES["RED_HIGHLIGHT"]};'>
                    Reversed / Reversal — will be deleted
                </span>
            </div>
        """, unsafe_allow_html=True)

    with col_after:
        st.markdown('<div class="section-header-green">After: Cleaned Data & Download</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="info-box-green">✅ {len(df_cleaned):,} row'
            f'{"s" if len(df_cleaned) != 1 else ""} remaining after cleanup</div>',
            unsafe_allow_html=True
        )
        st.dataframe(df_cleaned, height=AppConfig.DATAFRAME_HEIGHT, use_container_width=True)

        st.markdown(f"""
            <div class="stats-box">
                <div class="stat-row">
                    <span class="stat-label">Original Rows:</span>
                    <span class="stat-value">{len(df):,}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Reversed/Reversal Removed:</span>
                    <span class="stat-value" style="color:#dc2626;">{len(reversed_indices):,}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Final Rows:</span>
                    <span class="stat-value" style="color:#16a34a;">{len(df_cleaned):,}</span>
                </div>
            </div>
        """, unsafe_allow_html=True)

        st.write("---")

        # ── Download cleaned file ────────────────────────────────────────
        if reversed_indices:
            original = st.session_state.get("original_filename", "Excel_File.xlsx")
            base = re.sub(r"\.xlsx?$", "", original, flags=re.IGNORECASE)
            output_name = f"{base}_Cleaned.xlsx"

            if ('processed_file_data' not in st.session_state or
                    st.session_state.get('_reversed_cache_key') != cache_key):
                buffer = BytesIO()
                with st.spinner("Generating Excel file…"):
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        df_cleaned.to_excel(writer, index=False)
                    st.session_state.processed_file_data = buffer.getvalue()
                    st.session_state.processed_df = df_cleaned

            st.download_button(
                label="📥 Download Cleaned Excel File",
                data=st.session_state.processed_file_data,
                file_name=output_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
            )
        else:
            st.markdown(
                '<div class="info-box-green">No "Reversed" or "Reversal" rows — '
                'nothing to clean up.</div>',
                unsafe_allow_html=True
            )

    # ── Seamless forward to Book Segregation ─────────────────────────────
    st.write("")
    st.markdown("---")
    st.markdown(
        "<p style='text-align:center; color:#6b7280; margin-bottom:0.5rem;'>"
        "Ready to classify transactions into books?</p>",
        unsafe_allow_html=True
    )
    col_l, col_c, col_r = st.columns([2, 3, 2])
    with col_c:
        if st.button("📚 Continue to Book Segregation →",
                     use_container_width=True, type="primary",
                     key="continue_segregation_btn"):
            go_to_segregation()
