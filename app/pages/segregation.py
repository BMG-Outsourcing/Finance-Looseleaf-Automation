"""
Book Segregation module for the Excel Duplicate Delete application.

This module automatically classifies accounting transactions into three books:
- Cash Disbursement (expenses, payments)
- Cash Receipts (income, collections)
- General Journal (other transactions)

CLASSIFICATION LOGIC (corrected):
  A journal group is classified by finding its BANK line(s):
    - Bank account with debit  > 0  → MONEY CAME IN  → Cash Receipts
    - Bank account with credit > 0  → MONEY WENT OUT → Cash Disbursement
  If no bank line exists:
    - Trade Debtors / AR with credit > 0  → Cash Receipts
    - Accounts Payable with debit   > 0  → Cash Disbursement
  Manual entries or no match → General Journal

  Bank keywords: rcbc, westpac, macquarie  (case-insensitive, partial match)
"""

import streamlit as st
import pandas as pd
from io import BytesIO
import re
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))

try:
    from utils import load_logo
    from constants import CSS_STYLES, UI_LABELS, COLOR_CODES
    from config import AppConfig
except ImportError:
    class AppConfig:
        APP_TITLE = "Book Segregation Tool"
    def load_logo(): return None


# =============================================================================================
# CLASSIFIER
# =============================================================================================

# --- Keyword lists (extend here as needed) ---
BANK_KEYWORDS   = r"rcbc|westpac|macquarie"          # bank / cash accounts
AP_KEYWORDS     = r"accounts payable|trade creditors"
AR_KEYWORDS     = r"trade debtors|accounts receivable"
MANUAL_PATTERN  = r"-\s*manual\s*$"


class BookCategoryClassifier:
    """
    Classifier that segregates accounting data into Cash Disbursement,
    Cash Receipts, and General Journal books based on account patterns.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_column_name(self, df: pd.DataFrame, candidates: list) -> str:
        """Find column name from list of candidates (case-insensitive)."""
        cols = {c.lower().strip(): c for c in df.columns}
        for cand in candidates:
            if cand.lower() in cols:
                return cols[cand.lower()]
        return None

    def clean_reversals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove reversal entries from the dataframe."""
        target_cols = [
            self._get_column_name(df, ["narration"]),
            self._get_column_name(df, ["description"])
        ]
        target_cols = [c for c in target_cols if c]

        if not target_cols:
            return df

        pattern = r"(reversal of|reversed)"
        mask = pd.Series(False, index=df.index)
        for col in target_cols:
            mask |= df[col].astype(str).str.contains(pattern, case=False, regex=True, na=False)

        return df[~mask].copy()

    # ------------------------------------------------------------------
    # Core segregation
    # ------------------------------------------------------------------

    def segregate(self, df: pd.DataFrame) -> dict:
        """
        Segregate dataframe into three books.

        Priority (per journal group):
          1. Manual flag          → General Journal
          2. Bank debit  > 0      → Cash Receipts
          3. Bank credit > 0      → Cash Disbursement
          4. AR   credit > 0      → Cash Receipts
          5. AP   debit  > 0      → Cash Disbursement
          6. fallback             → General Journal
        """
        df = self.clean_reversals(df.copy())

        # --- Identify columns ---
        id_col      = self._get_column_name(df, ["journal id", "journal no", "id", "transaction id"])
        account_col = self._get_column_name(df, ["account", "account title", "account code"])
        debit_col   = self._get_column_name(df, ["debit", "dr"])
        credit_col  = self._get_column_name(df, ["credit", "cr"])
        narration_col = self._get_column_name(df, ["narration", "description", "memo"])
        date_col    = self._get_column_name(df, ["date"])

        if not all([id_col, account_col, debit_col, credit_col]):
            raise ValueError("Missing required columns (Journal ID, Account, Debit, Credit)")

        # ----------------------------------------------------------------
        # 1. FIX ID MISMATCH  (Description-embedded ID overrides column)
        # ----------------------------------------------------------------
        if date_col:
            extracted_ids = df[date_col].astype(str).str.extract(r'(?i)ID\s+(\d+)', expand=False)
            extracted_ids = extracted_ids.ffill()
            if not extracted_ids.isna().all():
                df[id_col] = extracted_ids.combine_first(df[id_col])

        df[id_col] = df[id_col].astype(str).str.strip()
        df[id_col] = df[id_col].replace(
            to_replace=r"^(total|grand total|nan|none|\s*)$",
            value=pd.NA,
            regex=True
        )
        df[id_col] = df[id_col].ffill()
        df = df.dropna(subset=[id_col])

        # --- Ensure numeric ---
        df[debit_col]  = pd.to_numeric(df[debit_col],  errors="coerce").fillna(0)
        df[credit_col] = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)

        # ----------------------------------------------------------------
        # 2. CLEAN "GHOST" ROWS
        # ----------------------------------------------------------------
        def is_blank(series):
            return series.isna() | series.astype(str).str.strip().str.lower().isin(['', 'nan', 'none'])

        if date_col:
            mask_junk = is_blank(df[date_col]) & is_blank(df[account_col]) & \
                        (df[debit_col] == 0) & (df[credit_col] == 0)
            df = df[~mask_junk].copy()

        # ----------------------------------------------------------------
        # 3. ROW-LEVEL FLAGS
        # ----------------------------------------------------------------
        acc_text  = df[account_col].astype(str).str.lower()
        narr_text = (
            df[narration_col].astype(str).str.lower()
            if narration_col else pd.Series("", index=df.index)
        )

        # Bank rows (account name or narration contains a bank keyword)
        is_bank = acc_text.str.contains(BANK_KEYWORDS, na=False) | \
                  narr_text.str.contains(BANK_KEYWORDS, na=False)

        is_ap = acc_text.str.contains(AP_KEYWORDS, na=False)
        is_ar = acc_text.str.contains(AR_KEYWORDS, na=False)

        # --- PRIMARY signals: bank-line direction ---
        # Money IN  (bank debited  → we received cash)
        df["__bank_debit"]  = is_bank & (df[debit_col]  > 0)
        # Money OUT (bank credited → we paid cash)
        df["__bank_credit"] = is_bank & (df[credit_col] > 0)

        # --- SECONDARY signals: AR/AP when no bank line present ---
        df["__ar_receipt"]   = is_ar & (df[credit_col] > 0)   # AR reduced → cash collected
        df["__ap_disburse"]  = is_ap & (df[debit_col]  > 0)   # AP reduced → cash paid

        # --- Manual flag ---
        df["__is_manual"] = False
        if date_col:
            df["__is_manual"] = df[date_col].astype(str).str.contains(
                MANUAL_PATTERN, case=False, regex=True, na=False
            )

        # ----------------------------------------------------------------
        # 4. GROUP-LEVEL CLASSIFICATION  (propagate signals per journal)
        # ----------------------------------------------------------------
        grp_manual      = df.groupby(id_col)["__is_manual"].transform("any")
        grp_bank_debit  = df.groupby(id_col)["__bank_debit"].transform("any")
        grp_bank_credit = df.groupby(id_col)["__bank_credit"].transform("any")
        grp_ar_receipt  = df.groupby(id_col)["__ar_receipt"].transform("any")
        grp_ap_disburse = df.groupby(id_col)["__ap_disburse"].transform("any")

        def assign_book(idx):
            # Priority 1: manual entries always → General Journal
            if grp_manual[idx]:
                return "General Journal"
            # Priority 2 & 3: bank line is the most reliable signal
            # If a group somehow has both (unusual), bank-debit wins (receipt)
            if grp_bank_debit[idx]:
                return "Cash Receipts"
            if grp_bank_credit[idx]:
                return "Cash Disbursement"
            # Priority 4 & 5: no bank line – use AR/AP
            if grp_ar_receipt[idx]:
                return "Cash Receipts"
            if grp_ap_disburse[idx]:
                return "Cash Disbursement"
            # Fallback
            return "General Journal"

        df["Book"] = df.index.to_series().apply(assign_book)

        # Drop helper columns
        helper_cols = ["__bank_debit", "__bank_credit", "__ar_receipt",
                       "__ap_disburse", "__is_manual"]
        df = df.drop(columns=helper_cols)

        results = {
            "Cash Disbursement": df[df["Book"] == "Cash Disbursement"].drop(columns="Book"),
            "Cash Receipts":     df[df["Book"] == "Cash Receipts"].drop(columns="Book"),
            "General Journal":   df[df["Book"] == "General Journal"].drop(columns="Book"),
        }

        # ----------------------------------------------------------------
        # 5. SORTING, TOTAL CALCULATION & SPACER INSERTION
        # ----------------------------------------------------------------
        if date_col:
            for key, book_df in results.items():
                if book_df.empty:
                    continue
                try:
                    book_df = book_df.copy()

                    book_df['__temp_sort_date']  = pd.to_datetime(book_df[date_col], errors='coerce')
                    book_df['__group_sort_date'] = book_df.groupby(id_col)['__temp_sort_date'].transform('min')

                    is_footer      = book_df[date_col].astype(str).str.contains(
                        r'^(Total|None|Grand Total)', case=False, na=False)
                    is_valid_date  = book_df['__temp_sort_date'].notna()

                    book_df['__row_rank'] = 0
                    book_df.loc[is_valid_date, '__row_rank'] = 1
                    book_df.loc[is_footer,     '__row_rank'] = 2

                    book_df = book_df.sort_values(
                        by=['__group_sort_date', id_col, '__row_rank'],
                        ascending=[True, True, True],
                        na_position='last'
                    )

                    sorted_groups = []
                    unique_ids    = book_df[id_col].unique()
                    blank_row     = pd.DataFrame(
                        [pd.NA] * len(book_df.columns),
                        index=book_df.columns
                    ).T
                    blank_row = blank_row.drop(
                        columns=['__temp_sort_date', '__group_sort_date', '__row_rank'],
                        errors='ignore'
                    )

                    for j_id in unique_ids:
                        group = book_df[book_df[id_col] == j_id].copy()
                        group = group.drop(
                            columns=['__temp_sort_date', '__group_sort_date', '__row_rank']
                        )

                        # Calculate totals on the Total row
                        total_mask = group[date_col].astype(str).str.contains(
                            r'^(Total|Grand Total)', case=False, na=False)
                        if total_mask.any():
                            group.loc[total_mask, debit_col]  = group.loc[~total_mask, debit_col].sum()
                            group.loc[total_mask, credit_col] = group.loc[~total_mask, credit_col].sum()

                        sorted_groups.append(group)
                        sorted_groups.append(blank_row)

                    if sorted_groups:
                        results[key] = pd.concat(sorted_groups[:-1], ignore_index=True)
                    else:
                        results[key] = book_df.drop(
                            columns=['__temp_sort_date', '__group_sort_date', '__row_rank'])

                except Exception:
                    pass   # leave book as-is on unexpected error

        return results


# =============================================================================================
# NAVIGATION HELPER
# =============================================================================================

def go_back_to_workspace():
    st.session_state.current_page = 'workspace'
    st.rerun()


# =============================================================================================
# UI RENDERING
# =============================================================================================

def render_segregation_page():
    """Render the book segregation page with clean colorful design."""

    st.markdown("""
        <style>
        .nav-bar {
            background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
            padding: 1.5rem 2rem;
            border-radius: 16px;
            margin-bottom: 2rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .nav-title    { color: white !important; font-size: 1.8rem; font-weight: 700; margin: 0; }
        .nav-subtitle { color: white !important; font-size: 1rem;   margin: 0.25rem 0 0 0; }
        .logo-container { display: flex; align-items: center; gap: 1rem; }
        .logo-image     { height: 50px; width: auto; }

        .section-header-orange {
            background: linear-gradient(135deg, #f97316 0%, #fb923c 100%);
            color: white; padding: 1rem 1.5rem; border-radius: 12px;
            font-size: 1.3rem; font-weight: 700; text-align: center;
            margin-bottom: 1.5rem; box-shadow: 0 4px 6px -1px rgba(249,115,22,.3);
        }
        .section-header-blue {
            background: linear-gradient(135deg, #0284c7 0%, #38bdf8 100%);
            color: white; padding: 1rem 1.5rem; border-radius: 12px;
            font-size: 1.3rem; font-weight: 700; text-align: center;
            margin-bottom: 1.5rem; box-shadow: 0 4px 6px -1px rgba(2,132,199,.3);
        }
        .section-header-green {
            background: linear-gradient(135deg, #16a34a 0%, #4ade80 100%);
            color: white; padding: 1rem 1.5rem; border-radius: 12px;
            font-size: 1.3rem; font-weight: 700; text-align: center;
            margin-bottom: 1.5rem; box-shadow: 0 4px 6px -1px rgba(22,163,74,.3);
        }

        .rule-card-orange { background:#fff7ed; padding:1rem; border-radius:12px; margin:.75rem 0; border-left:5px solid #f97316; }
        .rule-card-blue   { background:#eff6ff; padding:1rem; border-radius:12px; margin:.75rem 0; border-left:5px solid #0284c7; }
        .rule-card-green  { background:#f0fdf4; padding:1rem; border-radius:12px; margin:.75rem 0; border-left:5px solid #16a34a; }

        .stats-card-orange {
            background: linear-gradient(135deg,#fff7ed 0%,#fed7aa 100%);
            padding:1.5rem; border-radius:12px; text-align:center;
            border:3px solid #f97316; box-shadow:0 4px 6px -1px rgba(249,115,22,.2);
        }
        .stats-card-blue {
            background: linear-gradient(135deg,#eff6ff 0%,#bfdbfe 100%);
            padding:1.5rem; border-radius:12px; text-align:center;
            border:3px solid #0284c7; box-shadow:0 4px 6px -1px rgba(2,132,199,.2);
        }
        .stats-card-green {
            background: linear-gradient(135deg,#f0fdf4 0%,#bbf7d0 100%);
            padding:1.5rem; border-radius:12px; text-align:center;
            border:3px solid #16a34a; box-shadow:0 4px 6px -1px rgba(22,163,74,.2);
        }
        .stats-count { font-size:2rem; font-weight:700; margin-bottom:.5rem; }
        .stats-label { font-size:1rem; font-weight:600; }

        .info-box {
            background:#eff6ff; border-left:5px solid #0284c7;
            padding:1rem; border-radius:8px; margin:1rem 0;
            color:#0c4a6e; font-size:1rem;
        }
        .stDownloadButton > button {
            background: linear-gradient(135deg,#16a34a 0%,#22c55e 100%);
            color:white; border:none; font-size:1.1rem; font-weight:700;
            padding:1rem; border-radius:12px;
        }
        </style>
    """, unsafe_allow_html=True)

    logo_url = load_logo()

    if logo_url:
        st.markdown(f"""
            <div class="nav-bar">
                <div class="logo-container">
                    <img src="{logo_url}" class="logo-image" alt="Logo">
                    <div>
                        <h2 class="nav-title">{AppConfig.APP_TITLE}</h2>
                        <p class="nav-subtitle">Book Segregation</p>
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div class="nav-bar">
                <h2 class="nav-title">{AppConfig.APP_TITLE}</h2>
                <p class="nav-subtitle">Book Segregation</p>
            </div>""", unsafe_allow_html=True)

    col_back, _ = st.columns([1.5, 5.5])
    with col_back:
        if st.button("← Back to Workspace", use_container_width=True,
                     key="back_to_workspace_btn", type="secondary"):
            go_back_to_workspace()

    if st.session_state.get("processed_df") is not None:
        df = st.session_state.processed_df
        data_source = "processed data"
    elif st.session_state.get("df_original") is not None:
        df = st.session_state.df_original
        data_source = "original data"
    else:
        st.error("No data available. Please return to home and upload a file.")
        st.stop()

    st.markdown(f"""
        <div class="info-box">
            Segregating {data_source} with <strong>{len(df):,} rows</strong>
            into Cash Disbursement, Cash Receipts, and General Journal
        </div>""", unsafe_allow_html=True)

    with st.expander("Classification Rules", expanded=False):
        st.markdown("""
            <div class="rule-card-blue">
                <strong style="color:#0c4a6e;">Cash Receipts</strong>
                <p style="margin:.5rem 0 0 0;color:#1e40af;">
                Bank account (RCBC / Westpac / Macquarie) with a <b>debit</b> entry — money received<br>
                <i>or</i> Trade Debtors / Accounts Receivable with a credit entry (no bank line)
                </p>
            </div>
            <div class="rule-card-orange">
                <strong style="color:#9a3412;">Cash Disbursement</strong>
                <p style="margin:.5rem 0 0 0;color:#7c2d12;">
                Bank account (RCBC / Westpac / Macquarie) with a <b>credit</b> entry — money paid out<br>
                <i>or</i> Accounts Payable / Trade Creditors with a debit entry (no bank line)
                </p>
            </div>
            <div class="rule-card-green">
                <strong style="color:#14532d;">General Journal</strong>
                <p style="margin:.5rem 0 0 0;color:#065f46;">
                Entries whose date ends with "– Manual"<br>
                All other transactions not matched above
                </p>
            </div>
        """, unsafe_allow_html=True)

    try:
        classifier = BookCategoryClassifier()
        segregated = classifier.segregate(df)

        st.success("✅ Data successfully segregated")

        # Statistics
        st.markdown('<div class="section-header-orange">Summary Statistics</div>',
                    unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"""
                <div class="stats-card-orange">
                    <div class="stats-count" style="color:#9a3412;">{len(segregated["Cash Disbursement"]):,}</div>
                    <div class="stats-label" style="color:#7c2d12;">Cash Disbursement</div>
                </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
                <div class="stats-card-blue">
                    <div class="stats-count" style="color:#0c4a6e;">{len(segregated["Cash Receipts"]):,}</div>
                    <div class="stats-label" style="color:#1e40af;">Cash Receipts</div>
                </div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""
                <div class="stats-card-green">
                    <div class="stats-count" style="color:#14532d;">{len(segregated["General Journal"]):,}</div>
                    <div class="stats-label" style="color:#065f46;">General Journal</div>
                </div>""", unsafe_allow_html=True)

        st.write("")
        st.markdown("---")
        st.write("")

        # Display each book
        st.markdown('<div class="section-header-orange">Cash Disbursement Book</div>',
                    unsafe_allow_html=True)
        cd_df = segregated["Cash Disbursement"]
        if len(cd_df) > 0:
            st.dataframe(cd_df, height=min(400, len(cd_df) * 35 + 38), use_container_width=True)
        else:
            st.info("No transactions in this category")

        st.write("")
        st.markdown('<div class="section-header-blue">Cash Receipts Book</div>',
                    unsafe_allow_html=True)
        cr_df = segregated["Cash Receipts"]
        if len(cr_df) > 0:
            st.dataframe(cr_df, height=min(400, len(cr_df) * 35 + 38), use_container_width=True)
        else:
            st.info("No transactions in this category")

        st.write("")
        st.markdown('<div class="section-header-green">General Journal Book</div>',
                    unsafe_allow_html=True)
        gj_df = segregated["General Journal"]
        if len(gj_df) > 0:
            st.dataframe(gj_df, height=min(400, len(gj_df) * 35 + 38), use_container_width=True)
        else:
            st.info("No transactions in this category")

        # Download
        st.write("")
        st.markdown("---")
        st.write("")

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            segregated["Cash Disbursement"].to_excel(writer, sheet_name="Cash Disbursement", index=False)
            segregated["Cash Receipts"].to_excel(writer, sheet_name="Cash Receipts",     index=False)
            segregated["General Journal"].to_excel(writer, sheet_name="General Journal",  index=False)

        original    = st.session_state.get("original_filename", "Excel_File.xlsx")
        base        = re.sub(r"\.xlsx?$", "", original, flags=re.IGNORECASE)
        output_name = f"{base}_Segregated.xlsx"

        st.download_button(
            label=f"⬇️ Download {output_name}",
            data=buffer.getvalue(),
            file_name=output_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )

    except ValueError as e:
        st.error(f"Error: {str(e)}")
        st.info("Ensure your file contains: Journal ID, Account, Debit, and Credit columns")
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")