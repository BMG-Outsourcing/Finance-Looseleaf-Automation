"""
Home page module for the Finance Looseleaf Automation application.

Renders the landing page with file upload. Once a file is uploaded and
parsed, the app automatically navigates to the workspace.
"""

import streamlit as st
import pandas as pd
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils import load_logo, process_excel_with_formatting
from constants import CSS_STYLES, UI_LABELS, HELP_TEXTS
from config import AppConfig, initialize_session_state


def render_home_page():
    """Render the home page. Auto-forwards to workspace after upload."""

    logo_url = load_logo()

    # App Bar — logo + title only, no cluttering nav buttons
    if logo_url:
        st.markdown(f"""
            <div class="app-bar">
                <div class="app-bar-content">
                    <div class="logo-container">
                        <img src="{logo_url}" class="logo-image" alt="Logo">
                        <div class="logo-text">
                            <h1>{AppConfig.APP_TITLE}</h1>
                            <p>{UI_LABELS['APP_DESCRIPTION']}</p>
                        </div>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div class="app-bar">
                <div class="app-bar-content">
                    <h1>{AppConfig.APP_TITLE}</h1>
                    <p>{UI_LABELS['APP_DESCRIPTION']}</p>
                </div>
            </div>
        """, unsafe_allow_html=True)

    # Upload card
    st.markdown(f"""
        <div class="upload-container">
            <div class="upload-card">
                <h2 class="upload-header">{UI_LABELS['UPLOAD_HEADER']}</h2>
                <p class="upload-subheader">{UI_LABELS['UPLOAD_SUBHEADER']}</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        uploaded_file = st.file_uploader(
            "Upload an Excel file",
            type=AppConfig.SUPPORTED_FILE_TYPES,
            help=HELP_TEXTS['FILE_UPLOADER'],
            key="file_uploader_home"
        )

        if uploaded_file is not None:
            st.session_state.uploaded_file = uploaded_file
            st.session_state.original_filename = uploaded_file.name

            with st.spinner("Loading your Excel file…"):
                raw_df = pd.read_excel(
                    uploaded_file,
                    engine="openpyxl",
                    header=None
                )
                raw_df.columns = raw_df.iloc[4]
                df = raw_df.iloc[4:].reset_index(drop=True)
                df = df.dropna(axis=1, how="all")
                st.session_state.df_original = df.copy()

            st.success(f"File loaded — {len(df):,} rows found. Opening workspace…")

            # Auto-forward — no extra button click needed
            st.session_state.current_page = 'workspace'
            st.rerun()

    # How-it-works instructions
    st.markdown(f"""
        <div class="upload-container">
            <div class="upload-card">
                <div class="upload-instructions">
                    <h3>{UI_LABELS['HOW_IT_WORKS_TITLE']}</h3>
                    <ol class="instruction-list">
                        <li>
                            <span class="instruction-step">{UI_LABELS['INSTRUCTION_STEP_1']}:</span>
                            {UI_LABELS['INSTRUCTION_DESC_1']}
                        </li>
                        <li>
                            <span class="instruction-step">{UI_LABELS['INSTRUCTION_STEP_2']}:</span>
                            {UI_LABELS['INSTRUCTION_DESC_2']}
                        </li>
                        <li>
                            <span class="instruction-step">{UI_LABELS['INSTRUCTION_STEP_3']}:</span>
                            {UI_LABELS['INSTRUCTION_DESC_3']}
                        </li>
                        <li>
                            <span class="instruction-step">{UI_LABELS['INSTRUCTION_STEP_4']}:</span>
                            {UI_LABELS['INSTRUCTION_DESC_4']}
                        </li>
                        <li>
                            <span class="instruction-step">{UI_LABELS['INSTRUCTION_STEP_5']}:</span>
                            {UI_LABELS['INSTRUCTION_DESC_5']}
                        </li>
                    </ol>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)
