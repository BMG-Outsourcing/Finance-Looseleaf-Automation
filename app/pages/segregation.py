"""
Book Segregation module for the Excel Duplicate Delete application.

CLASSIFICATION LOGIC (simplified and corrected):

  For each journal group (same Journal ID):

  STEP 1 — Check for BANK rows: does any row in the group have a bank
           keyword (rcbc, westpac, macquarie) in the ACCOUNT column?

  STEP 2 — NO bank account found → General Journal. Done.

  STEP 3 — Bank account found → look at Debit/Credit on that bank row:
           • Bank Debit  > 0  → Cash Receipts     (money came IN)
           • Bank Credit > 0  → Cash Disbursement (money went OUT)
           • Both sides non-zero → bank-debit wins (receipt)

  STEP 4 — Manual entries (date ends with "- Manual") → General Journal,
           regardless of accounts present.

  AR / AP / Trade Debtors / Trade Creditors are NOT classification signals.
  Only the bank account line drives the Cash Receipts / Disbursement split.
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



# Constants — edit here to add / remove bank keywords

BANK_KEYWORDS  = r"rcbc|westpac|macquarie"   # partial, case-insensitive
MANUAL_PATTERN = r"-\s*manual\s*$"



# CLASSIFIER


class BookCategoryClassifier:

    def _get_col(self, df: pd.DataFrame, candidates: list) -> str:
        """Return first matching column (case-insensitive)."""
        cols = {c.lower().strip(): c for c in df.columns}
        for cand in candidates:
            if cand.lower() in cols:
                return cols[cand.lower()]
        return None

    # ── reversal removal ─────────────────────────────────────────────────────
    def clean_reversals(self, df: pd.DataFrame) -> pd.DataFrame:
        cols = [self._get_col(df, ["narration"]), self._get_col(df, ["description"])]
        cols = [c for c in cols if c]
        if not cols:
            return df
        mask = pd.Series(False, index=df.index)
        for col in cols:
            mask |= df[col].astype(str).str.contains(
                r"(reversal of|reversed)", case=False, regex=True, na=False
            )
        return df[~mask].copy()

    # ── main entry point ─────────────────────────────────────────────────────
    def segregate(self, df: pd.DataFrame) -> dict:
        df = self.clean_reversals(df.copy())

        # Identify columns
        id_col   = self._get_col(df, ["journal id", "journal no", "id", "transaction id"])
        acc_col  = self._get_col(df, ["account", "account title", "account code"])
        dr_col   = self._get_col(df, ["debit", "dr"])
        cr_col   = self._get_col(df, ["credit", "cr"])
        date_col = self._get_col(df, ["date"])

        if not all([id_col, acc_col, dr_col, cr_col]):
            raise ValueError(
                "Missing required columns — need: Journal ID, Account, Debit, Credit"
            )

        # ── Fix ID mismatch (ID embedded inside a date cell) ─────────────────
        if date_col:
            extracted = (
                df[date_col].astype(str)
                .str.extract(r'(?i)ID\s+(\d+)', expand=False)
                .ffill()
            )
            if not extracted.isna().all():
                df[id_col] = extracted.combine_first(df[id_col])

        df[id_col] = (
            df[id_col].astype(str).str.strip()
            .replace(r"^(total|grand total|nan|none|\s*)$", pd.NA, regex=True)
            .ffill()
        )
        df = df.dropna(subset=[id_col])

        # ── Numeric amounts ───────────────────────────────────────────────────
        df[dr_col] = pd.to_numeric(df[dr_col], errors="coerce").fillna(0)
        df[cr_col] = pd.to_numeric(df[cr_col], errors="coerce").fillna(0)

        # ── Drop ghost rows ───────────────────────────────────────────────────
        def _blank(s):
            return s.isna() | s.astype(str).str.strip().str.lower().isin(
                ["", "nan", "none"]
            )

        if date_col:
            ghost = (
                _blank(df[date_col]) & _blank(df[acc_col]) &
                (df[dr_col] == 0) & (df[cr_col] == 0)
            )
            df = df[~ghost].copy()

        # ══════════════════════════════════════════════════════════════════════
        # ROW-LEVEL FLAGS
        # ══════════════════════════════════════════════════════════════════════

        acc_lower = df[acc_col].astype(str).str.lower()

        # Is this row itself a bank account row?
        is_bank_row = acc_lower.str.contains(BANK_KEYWORDS, na=False)

        # Bank row signals
        df["__bank_dr"] = is_bank_row & (df[dr_col] > 0)   # money IN
        df["__bank_cr"] = is_bank_row & (df[cr_col] > 0)   # money OUT
        df["__has_bank"] = is_bank_row                      # any bank row

        # Manual flag
        df["__manual"] = False
        if date_col:
            df["__manual"] = df[date_col].astype(str).str.contains(
                MANUAL_PATTERN, case=False, regex=True, na=False
            )

        # ══════════════════════════════════════════════════════════════════════
        # GROUP-LEVEL PROPAGATION
        # ══════════════════════════════════════════════════════════════════════
        grp_manual   = df.groupby(id_col)["__manual"].transform("any")
        grp_has_bank = df.groupby(id_col)["__has_bank"].transform("any")
        grp_bank_dr  = df.groupby(id_col)["__bank_dr"].transform("any")
        grp_bank_cr  = df.groupby(id_col)["__bank_cr"].transform("any")

        # ══════════════════════════════════════════════════════════════════════
        # BOOK ASSIGNMENT
        # ══════════════════════════════════════════════════════════════════════
        def _assign(idx):
            # Manual entries always → General Journal
            if grp_manual[idx]:
                return "General Journal"
            # No bank account at all → General Journal
            if not grp_has_bank[idx]:
                return "General Journal"
            # Bank account present → direction decides
            if grp_bank_dr[idx]:
                return "Cash Receipts"       # bank debited  → received cash
            if grp_bank_cr[idx]:
                return "Cash Disbursement"   # bank credited → paid cash
            # Bank row exists but zero amount → General Journal
            return "General Journal"

        df["Book"] = df.index.to_series().apply(_assign)
        df = df.drop(columns=["__bank_dr", "__bank_cr", "__has_bank", "__manual"])

        results = {
            "Cash Disbursement": df[df["Book"] == "Cash Disbursement"].drop(columns="Book"),
            "Cash Receipts":     df[df["Book"] == "Cash Receipts"].drop(columns="Book"),
            "General Journal":   df[df["Book"] == "General Journal"].drop(columns="Book"),
        }

        # ══════════════════════════════════════════════════════════════════════
        # SORT + TOTAL RECALCULATION + BLANK SPACERS
        # ══════════════════════════════════════════════════════════════════════
        if date_col:
            for key, bdf in results.items():
                if bdf.empty:
                    continue
                try:
                    bdf = bdf.copy()
                    bdf["__sd"] = pd.to_datetime(bdf[date_col], errors="coerce")
                    bdf["__gd"] = bdf.groupby(id_col)["__sd"].transform("min")

                    is_footer = bdf[date_col].astype(str).str.contains(
                        r"^(Total|Grand Total)", case=False, na=False
                    )
                    bdf["__rank"] = 0
                    bdf.loc[bdf["__sd"].notna(), "__rank"] = 1
                    bdf.loc[is_footer, "__rank"] = 2

                    bdf = bdf.sort_values(
                        ["__gd", id_col, "__rank"],
                        ascending=[True, True, True],
                        na_position="last",
                    )

                    blank = (
                        pd.DataFrame([pd.NA] * len(bdf.columns), index=bdf.columns)
                        .T.drop(columns=["__sd", "__gd", "__rank"], errors="ignore")
                    )

                    groups = []
                    for j_id in bdf[id_col].unique():
                        grp = bdf[bdf[id_col] == j_id].copy().drop(
                            columns=["__sd", "__gd", "__rank"]
                        )
                        # Recalculate Total row amounts
                        tot = grp[date_col].astype(str).str.contains(
                            r"^(Total|Grand Total)", case=False, na=False
                        )
                        if tot.any():
                            grp.loc[tot, dr_col] = grp.loc[~tot, dr_col].sum()
                            grp.loc[tot, cr_col] = grp.loc[~tot, cr_col].sum()
                        groups.append(grp)
                        groups.append(blank)

                    results[key] = (
                        pd.concat(groups[:-1], ignore_index=True)
                        if groups
                        else bdf.drop(columns=["__sd", "__gd", "__rank"])
                    )
                except Exception:
                    pass

        return results



# NAVIGATION


def go_back_to_workspace():
    st.session_state.current_page = "workspace"
    st.rerun()



# UI


def render_segregation_page():

    st.markdown("""
        <style>
        .nav-bar {
            background:linear-gradient(135deg,#1e293b 0%,#334155 100%);
            padding:1.5rem 2rem; border-radius:16px; margin-bottom:2rem;
            box-shadow:0 4px 6px -1px rgba(0,0,0,.1);
        }
        .nav-title    {color:white!important;font-size:1.8rem;font-weight:700;margin:0;}
        .nav-subtitle {color:white!important;font-size:1rem;margin:.25rem 0 0 0;}
        .logo-container{display:flex;align-items:center;gap:1rem;}
        .logo-image{height:50px;width:auto;}
        .section-header-orange{
            background:linear-gradient(135deg,#f97316 0%,#fb923c 100%);
            color:white;padding:1rem 1.5rem;border-radius:12px;
            font-size:1.3rem;font-weight:700;text-align:center;
            margin-bottom:1.5rem;box-shadow:0 4px 6px -1px rgba(249,115,22,.3);
        }
        .section-header-blue{
            background:linear-gradient(135deg,#0284c7 0%,#38bdf8 100%);
            color:white;padding:1rem 1.5rem;border-radius:12px;
            font-size:1.3rem;font-weight:700;text-align:center;
            margin-bottom:1.5rem;box-shadow:0 4px 6px -1px rgba(2,132,199,.3);
        }
        .section-header-green{
            background:linear-gradient(135deg,#16a34a 0%,#4ade80 100%);
            color:white;padding:1rem 1.5rem;border-radius:12px;
            font-size:1.3rem;font-weight:700;text-align:center;
            margin-bottom:1.5rem;box-shadow:0 4px 6px -1px rgba(22,163,74,.3);
        }
        .rule-card-orange{background:#fff7ed;padding:1rem;border-radius:12px;margin:.75rem 0;border-left:5px solid #f97316;}
        .rule-card-blue  {background:#eff6ff;padding:1rem;border-radius:12px;margin:.75rem 0;border-left:5px solid #0284c7;}
        .rule-card-green {background:#f0fdf4;padding:1rem;border-radius:12px;margin:.75rem 0;border-left:5px solid #16a34a;}
        .stats-card-orange{background:linear-gradient(135deg,#fff7ed 0%,#fed7aa 100%);padding:1.5rem;border-radius:12px;text-align:center;border:3px solid #f97316;box-shadow:0 4px 6px -1px rgba(249,115,22,.2);}
        .stats-card-blue  {background:linear-gradient(135deg,#eff6ff 0%,#bfdbfe 100%);padding:1.5rem;border-radius:12px;text-align:center;border:3px solid #0284c7;box-shadow:0 4px 6px -1px rgba(2,132,199,.2);}
        .stats-card-green {background:linear-gradient(135deg,#f0fdf4 0%,#bbf7d0 100%);padding:1.5rem;border-radius:12px;text-align:center;border:3px solid #16a34a;box-shadow:0 4px 6px -1px rgba(22,163,74,.2);}
        .stats-count{font-size:2rem;font-weight:700;margin-bottom:.5rem;}
        .stats-label{font-size:1rem;font-weight:600;}
        .info-box{background:#eff6ff;border-left:5px solid #0284c7;padding:1rem;border-radius:8px;margin:1rem 0;color:#0c4a6e;font-size:1rem;}
        .stDownloadButton>button{background:linear-gradient(135deg,#16a34a 0%,#22c55e 100%);color:white;border:none;font-size:1.1rem;font-weight:700;padding:1rem;border-radius:12px;}
        </style>
    """, unsafe_allow_html=True)

    logo_url = load_logo()
    if logo_url:
        st.markdown(f"""
            <div class="nav-bar"><div class="logo-container">
                <img src="{logo_url}" class="logo-image" alt="Logo">
                <div>
                    <h2 class="nav-title">{AppConfig.APP_TITLE}</h2>
                    <p class="nav-subtitle">Book Segregation</p>
                </div>
            </div></div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div class="nav-bar">
                <h2 class="nav-title">{AppConfig.APP_TITLE}</h2>
                <p class="nav-subtitle">Book Segregation</p>
            </div>""", unsafe_allow_html=True)

    col_back, _ = st.columns([1.5, 5.5])
    with col_back:
        if st.button("← Back to Workspace", use_container_width=True,
                     key="back_btn", type="secondary"):
            go_back_to_workspace()

    if st.session_state.get("processed_df") is not None:
        df, src = st.session_state.processed_df, "processed data"
    elif st.session_state.get("df_original") is not None:
        df, src = st.session_state.df_original, "original data"
    else:
        st.error("No data available. Please return to home and upload a file.")
        st.stop()

    st.markdown(f"""
        <div class="info-box">
            Segregating {src} with <strong>{len(df):,} rows</strong>
            into Cash Disbursement, Cash Receipts, and General Journal
        </div>""", unsafe_allow_html=True)

    with st.expander("Classification Rules", expanded=False):
        st.markdown(f"""
            <div class="rule-card-blue">
                <strong style="color:#0c4a6e;">Cash Receipts</strong>
                <p style="margin:.5rem 0 0 0;color:#1e40af;">
                The journal group contains a bank account
                (<code>rcbc / westpac / macquarie</code>) in the <b>Account</b> column
                AND that bank row has <b>Debit &gt; 0</b> — cash was received.
                </p>
            </div>
            <div class="rule-card-orange">
                <strong style="color:#9a3412;">Cash Disbursement</strong>
                <p style="margin:.5rem 0 0 0;color:#7c2d12;">
                The journal group contains a bank account
                (<code>rcbc / westpac / macquarie</code>) in the <b>Account</b> column
                AND that bank row has <b>Credit &gt; 0</b> — cash was paid out.
                </p>
            </div>
            <div class="rule-card-green">
                <strong style="color:#14532d;">General Journal</strong>
                <p style="margin:.5rem 0 0 0;color:#065f46;">
                The journal group has <b>no bank account</b> in the Account column,
                or the entry is flagged as "Manual".
                AR / AP / Trade Debtors / Creditors do <b>not</b> affect classification.
                </p>
            </div>
        """, unsafe_allow_html=True)

    try:
        classifier = BookCategoryClassifier()
        segregated = classifier.segregate(df)
        st.success("✅ Data successfully segregated")

        st.markdown('<div class="section-header-orange">Summary Statistics</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""<div class="stats-card-orange">
                <div class="stats-count" style="color:#9a3412;">{len(segregated["Cash Disbursement"]):,}</div>
                <div class="stats-label" style="color:#7c2d12;">Cash Disbursement</div>
                </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""<div class="stats-card-blue">
                <div class="stats-count" style="color:#0c4a6e;">{len(segregated["Cash Receipts"]):,}</div>
                <div class="stats-label" style="color:#1e40af;">Cash Receipts</div>
                </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""<div class="stats-card-green">
                <div class="stats-count" style="color:#14532d;">{len(segregated["General Journal"]):,}</div>
                <div class="stats-label" style="color:#065f46;">General Journal</div>
                </div>""", unsafe_allow_html=True)

        st.write(""); st.markdown("---"); st.write("")

        for header_class, label, key in [
            ("section-header-orange", "Cash Disbursement Book", "Cash Disbursement"),
            ("section-header-blue",   "Cash Receipts Book",     "Cash Receipts"),
            ("section-header-green",  "General Journal Book",   "General Journal"),
        ]:
            st.markdown(f'<div class="{header_class}">{label}</div>', unsafe_allow_html=True)
            bdf = segregated[key]
            if len(bdf) > 0:
                st.dataframe(bdf, height=min(400, len(bdf)*35+38), use_container_width=True)
            else:
                st.info("No transactions in this category")
            st.write("")

        st.markdown("---"); st.write("")

        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            segregated["Cash Disbursement"].to_excel(
                writer, sheet_name="Cash Disbursement", index=False)
            segregated["Cash Receipts"].to_excel(
                writer, sheet_name="Cash Receipts",     index=False)
            segregated["General Journal"].to_excel(
                writer, sheet_name="General Journal",   index=False)

        base = re.sub(r"\.xlsx?$", "",
                      st.session_state.get("original_filename", "Excel_File.xlsx"),
                      flags=re.IGNORECASE)
        out  = f"{base}_Segregated.xlsx"

        st.download_button(
            label=f"⬇️ Download {out}",
            data=buf.getvalue(),
            file_name=out,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )

    except ValueError as e:
        st.error(f"Error: {str(e)}")
        st.info("Ensure your file has: Journal ID, Account, Debit, and Credit columns")
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")