# FRT Parser

Converts the RCMP [Firearms Reference Table](https://www.rcmp-grc.gc.ca/en/firearms/firearms-reference-table) (FRT) PDF into a structured database.

> **Disclaimer:** The data in the generated output is parsed automatically from a PDF and may contain errors, omissions, or misaligned fields. It should not be used as a source of legal advice or relied upon for any official, legal, or safety-critical purpose. Always refer to the [official RCMP Firearms Reference Table](https://www.rcmp-grc.gc.ca/en/firearms/firearms-reference-table) for accurate and authoritative information.

---

## Quick start — `runAll.py`

Runs the full pipeline in one command: parse → split → create DB → import → cleanup.

```bash
python runAll.py frt-0504.pdf --database FRT
python runAll.py frt-0504.pdf --host myserver --database FRT --user root --password secret
```

| Argument | Description |
|---|---|
| `pdf` | Path to the FRT PDF |
| `--host` / `-s` | Database host (default: `localhost`) |
| `--port` | Database port (default: `3306`) |
| `--database` / `-d` | Target database name (required) |
| `--user` / `-u` | Database user (default: `root`) |
| `--password` / `-p` | Database password (default: empty) |
| `--workers` | Worker processes for parsing (default: `1`, `0` = auto) |

**Steps performed:**

1. `parse_frt.py` — extracts all FRN records from the PDF to a temp JSON file
2. `split_frn.py` — splits the PDF into one PDF per FRN in a temp directory
3. `createDb.py` — drops and recreates all database tables, triggers, and indexes
4. `importIntoDb.py` — loads the JSON records and per-FRN PDFs into the database
5. Deletes the temp JSON file and temp PDF directory

---

## Scripts

### `install.py`

Installs all required Python packages.

```bash
python install.py
```

Installs: `pdfplumber`, `pypdf`, `mysql-connector-python`.

---

### `parse_frt.py`

Parses the FRT PDF and writes one JSON record per parent FRN.

```bash
python parse_frt.py <input.pdf> [output.json] [--workers N]
```

| Argument | Description |
|---|---|
| `input.pdf` | Path to the FRT PDF |
| `output.json` | Output file (default: `frt_data.json`) |
| `--workers N` | Worker processes. `1` (default) = sequential. `0` = auto. |

**Examples**

```bash
python parse_frt.py frt-0504.pdf output.json
python parse_frt.py frt-0504.pdf output.json --workers 0
python parse_frt.py frt-0504.pdf output.json --workers 8
```

Accepts both `.json` (array) and `.jsonl` output. Both formats are accepted by `importIntoDb.py`.

---

### `split_frn.py`

Splits the FRT PDF into one PDF per FRN. Pages are grouped by the FRN printed at the top; pages without an FRN are skipped. Sub-entry pages (e.g. FRN `12345-1`) are grouped under the parent (`12345.pdf`).

Scanning and writing run concurrently: as the scanner detects FRN transitions it enqueues completed FRNs for writer threads to flush to disk.

```bash
python split_frn.py <pdf> <pages_dir> [-w N] [--top-chars N]
```

| Argument | Description |
|---|---|
| `pdf` | Path to the FRT PDF |
| `pages_dir` | Output directory for per-FRN PDFs |
| `-w` / `--writers` | Writer threads (default: `4`) |
| `--top-chars` | Characters from page top to search for FRN (default: `400`) |

**Example**

```bash
python split_frn.py frt-0504.pdf frn_pages/
```

---

### `createDb.py`

Creates the MariaDB/MySQL database schema. Drops and recreates all tables, indexes, triggers, and the `fulltextify` stored function on each run.

```bash
python createDb.py --database FRT
python createDb.py --host myserver --database FRT --user root --password secret
```

| Argument | Description |
|---|---|
| `--host` / `-s` | Database host (default: `localhost`) |
| `--port` | Database port (default: `3306`) |
| `--database` / `-d` | Target database name (required) |
| `--user` / `-u` | Database user (default: `root`) |
| `--password` / `-p` | Database password (default: empty) |

**Tables created**

| Table | Description |
|---|---|
| `dbo_frn` | One row per parent FRN |
| `dbo_frnSubEntry` | Calibre / shots / barrel length sub-entries |
| `dbo_frnNote` | Notes section bullets |
| `dbo_frnCrossReference` | Cross-referenced FRNs |
| `dbo_frnPdf` | Base64-encoded per-FRN PDFs |

Each table has a `fullText` column populated automatically by before-insert/before-update triggers via the `fulltextify()` stored function. A `FULLTEXT` index on each `fullText` column enables full-text search.

---

### `importIntoDb.py`

Imports a `parse_frt.py` output file and optionally a directory of per-FRN PDFs into the database. Accepts both JSON array and JSONL formats.

```bash
python importIntoDb.py output.jsonl --database FRT
python importIntoDb.py output.jsonl --database FRT --pages-dir frn_pages/
```

| Argument | Description |
|---|---|
| `file` | Path to the JSON or JSONL input file |
| `--host` / `-s` | Database host (default: `localhost`) |
| `--port` | Database port (default: `3306`) |
| `--database` / `-d` | Target database name (required) |
| `--user` / `-u` | Database user (default: `root`) |
| `--password` / `-p` | Database password (default: empty) |
| `--pages-dir` / `-P` | Directory of per-FRN PDFs to load into `dbo_frnPdf` |

---

## Output format

Each object in the JSON output represents one parent FRN:

```json
{
  "frn": "166062",
  "make": "2 Vets Arms",
  "model": "2VA-10",
  "manufacturer": "2 Vets Arms",
  "level": "Manufacturer Specifications and Commercial Customization",
  "type": "Rifle",
  "action": "Semi-Automatic",
  "country": "UNITED STATES OF AMERICA",
  "legal_classification": "Prohibited",
  "serial_numbering": "serial-numbered on the receiver/frame.",
  "year_dates": "...",
  "importer": "...",
  "notes": {
    "make": ["the 2 Vets Arms name and logo is marked on the firearm."],
    "model": ["\"2VA-10\" is marked on the right side of the receiver/frame.", "..."],
    "action": ["gas-operated."]
  },
  "sub_entries": [
    {
      "frn": "166062-1",
      "calibre": "308 WIN",
      "shots": "5",
      "barrel_length": "457",
      "legal_classification": "Prohibited"
    }
  ],
  "cross_references": [
    { "frn": "176685", "description": "Stealth Arms 1911 Government ..." }
  ]
}
```

Fields are omitted when absent or when the PDF value is "No Data". `notes` keys are camelCase versions of the sub-section headings from each page's Notes section. Each value is a list of bullet strings.

---

## Requirements

```
pdfplumber
pypdf
mysql-connector-python
```

Install all at once:

```bash
python install.py
```

Or manually:

```bash
pip install pdfplumber pypdf mysql-connector-python
```

Python 3.10 or later is required (`str | None` union type syntax).

The target database must exist before running `createDb.py` or `runAll.py`. The database user needs `CREATE`, `DROP`, `INSERT`, `TRIGGER`, and `CREATE ROUTINE` privileges.
