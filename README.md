# Finance Looseleaf Automation

A web-based tool built for **BMG Outsourcing INC.** that automates the cleaning and classification of journal entry data exported from accounting systems into Excel files.

---

## What It Does

The tool takes a raw Excel export of journal entries and processes it in two sequential stages:

1. **Reversal Cleanup** — removes reversed/voided journal entries from the data
2. **Book Segregation** — classifies the cleaned entries into the correct accounting books

---

## The Workflow

### Stage 0 — Upload

The user uploads an Excel file (.xlsx or .xls). The tool expects the data to start at a specific row (row 5 is treated as the header), so it automatically skips the rows above it and strips any completely empty columns before loading the data into the workspace.

---

### Stage 1 — Reversal Cleanup (Workspace)

Once the file is loaded, the tool scans every journal entry group and flags entries that are reversals or have been reversed.

**How it identifies entries to remove:**

- It looks for journal groups whose header contains the words **"Reversed"** or **"Reversal"** (case-insensitive).
- When a group is flagged (e.g., `ID 104308 Reversed: ...`), **that entire group** is marked for deletion — including its header row, all transaction lines, the `Total` row, and the blank separator row that follows it.
- If the header also says **"Reversal of ID XXXXX"**, the *original entry* that was reversed (e.g., `ID 104305`) is **also** deleted alongside the reversing entry.

This means both sides of a reversal pair are wiped out together, leaving only legitimate, non-reversed transactions.

The workspace shows the data in a **before-and-after** split view:
- **Left panel:** The original data with reversed rows highlighted in red so the user can see exactly what will be removed.
- **Right panel:** A live preview of the cleaned data with a row count summary.

The user can then download the cleaned file. After downloading, the tool prompts whether to proceed to Book Segregation.

---

### Stage 2 — Book Segregation

The cleaned data (either downloaded and re-uploaded, or carried over directly) is classified into three accounting books:

| Book | Description |
|---|---|
| **Cash Receipts** | Money coming *in* to a bank account |
| **Cash Disbursement** | Money going *out* of a bank account |
| **General Journal** | Everything else |

**How classification works:**

The tool looks at the **Account** column within each journal group (entries sharing the same Journal ID) and applies these rules in order:

1. **Manual entries first** — If the journal entry's date field ends with `- Manual`, it goes to **General Journal**. No further checks.

2. **Bank account check** — The tool looks for rows in the group where the Account is one of the recognised bank accounts:
   - `RCBC`
   - `Westpac`
   - `Macquarie`

3. **If a bank account row is found:**
   - Bank row has a **Debit amount > 0** → **Cash Receipts** (money received into the bank)
   - Bank row has a **Credit amount > 0** → **Cash Disbursement** (money paid out of the bank)

4. **If no bank account row is found** → **General Journal**

> **Important:** AR (Accounts Receivable) and AP (Accounts Payable) account rows are completely ignored during classification. Only bank account rows drive the decision.

The tool also automatically removes any entries whose narration/description contains "reversal of" or "reversed" as a secondary safety pass before classifying.

After classification, the tool shows:
- A **summary count** of how many rows landed in each book
- A **data table** for each of the three books
- A **download button** that exports all three books as separate sheets in a single Excel file, named `[original filename]_Segregated.xlsx`

The entries within each book are sorted chronologically by their earliest transaction date, with `Total` rows kept at the bottom of their respective journal groups and a blank row separating each group.

---

## Output Files

| File | Contents |
|---|---|
| `[filename]_Cleaned.xlsx` | Original data with all reversed/reversal entries removed |
| `[filename]_Segregated.xlsx` | Three-sheet workbook: Cash Disbursement, Cash Receipts, General Journal |

---

## Intended Use

This tool is designed for the finance team at BMG Outsourcing INC. to process looseleaf journal exports efficiently by replacing what would otherwise be a manual process of hunting for reversal pairs and sorting entries into the correct books by hand.
