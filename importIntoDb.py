#!/usr/bin/env python3
"""
importIntoDb.py — Import a parse_frt.py output file into the FRT database.

Accepts both JSON array and JSONL formats. Records are inserted in batches for
performance. The fullText column on each table is populated automatically by
the database triggers (no need to supply it here).

Usage:
  python importIntoDb.py output.jsonl --database FRT
  python importIntoDb.py output.jsonl --host myserver --database FRT --user root --password secret
"""

import argparse
import base64
import glob
import json
import os
import sys
import time

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    print("Error: mysql-connector-python is required.  Install with:  pip install mysql-connector-python")
    sys.exit(1)


BATCH_SIZE = 100

_INSERT_FRN = """
    insert into dbo_frn
        (frn, make, model, manufacturer, level, type, action, country,
         legal_classification, serial_numbering, year_dates, importer)
    values
        (ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''),
         ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''),
         ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''))
"""

_INSERT_SUB = """
    insert into dbo_frnSubEntry
        (frn, sub_frn, calibre, shots, barrel_length, legal_classification)
    values
        (ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''),
         ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''))
"""

_INSERT_NOTE = """
    insert into dbo_frnNote
        (frn, note_key, bullet_index, note_value)
    values
        (ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, 0), ifnull(%s, ''))
"""

_INSERT_XREF = """
    insert into dbo_frnCrossReference
        (frn, ref_frn, description)
    values
        (ifnull(%s, ''), ifnull(%s, ''), ifnull(%s, ''))
"""

_INSERT_PDF = """
    insert ignore into dbo_frnPdf (frn, pdf64)
    values (%s, %s)
"""

PDF_BATCH_SIZE = 25


def _get(record, key):
    """Return the field value, or None for missing/empty fields."""
    return record.get(key) or None


def import_pdfs(pages_dir: str, host: str, port: int, database: str, user: str, password: str) -> None:
    pdf_files = sorted(glob.glob(os.path.join(pages_dir, '*.pdf')))
    if not pdf_files:
        print(f"No PDF files found in {pages_dir}")
        return

    total_files = len(pdf_files)
    print(f"Source: {pages_dir}  ({total_files:,} files)")
    print("=" * 60)

    print("Connecting...", end=" ", flush=True)
    conn = mysql.connector.connect(
        host=host, port=port, database=database,
        user=user, password=password, charset="utf8mb4",
    )
    print("connected")

    cursor = conn.cursor()
    cursor.execute("SET GLOBAL max_allowed_packet = 268435456")  # 256 MB — base64 PDFs can be large
    rows = []
    total   = 0
    skipped = 0
    start = time.monotonic()

    def flush():
        nonlocal total, skipped
        if not rows:
            return
        cursor.executemany(_INSERT_PDF, rows)
        inserted = cursor.rowcount  # rows actually inserted (skipped rows not counted)
        conn.commit()
        if inserted >= 0 and inserted < len(rows):
            # Identify which FRNs were silently dropped by INSERT IGNORE
            batch_frns = [r[0] for r in rows]
            placeholders = ','.join(['%s'] * len(batch_frns))
            cursor.execute(
                f"select frn from dbo_frn where frn in ({placeholders})",
                batch_frns,
            )
            found = {r[0] for r in cursor.fetchall()}
            for frn in batch_frns:
                if frn not in found:
                    sys.stdout.write(f"\n  [skip] FRN {frn} — not in dbo_frn, PDF skipped")
                    skipped += 1
        total += len(rows)
        rows.clear()

    try:
        for path in pdf_files:
            frn = os.path.splitext(os.path.basename(path))[0]
            with open(path, 'rb') as f:
                pdf64 = base64.b64encode(f.read()).decode('ascii')
            rows.append((frn, pdf64))

            if len(rows) >= PDF_BATCH_SIZE:
                flush()
                elapsed = time.monotonic() - start
                rate = total / elapsed if elapsed > 0 else 0
                sys.stdout.write(
                    f"\r  {total:,}/{total_files:,}  ({rate:.0f} files/s)  "
                )
                sys.stdout.flush()

        flush()
    finally:
        conn.close()

    elapsed = time.monotonic() - start
    sys.stdout.write("\n")
    print("=" * 60)
    print(f"Done in {_format_duration(elapsed)}")
    print(f"  dbo_frnPdf            {total - skipped:>8,} rows  ({skipped:,} skipped)")


def _iter_records(path):
    """Yield one record dict at a time from a JSON array or JSONL file."""
    with open(path, 'r', encoding='utf-8') as f:
        first_char = f.read(1)
        f.seek(0)
        if first_char == '[':
            for record in json.load(f):
                yield record
        else:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _format_duration(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def import_records(path, host, port, database, user, password):
    print("Connecting...", end=" ", flush=True)
    conn = mysql.connector.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        charset="utf8mb4",
    )
    print("connected")
    print(f"Source: {path}")
    print("=" * 60)

    cursor = conn.cursor()

    frn_rows  = []
    sub_rows  = []
    note_rows = []
    xref_rows = []

    total_frn  = 0
    total_sub  = 0
    total_note = 0
    total_xref = 0
    start_time = time.monotonic()

    def flush():
        nonlocal total_frn, total_sub, total_note, total_xref
        if frn_rows:
            cursor.executemany(_INSERT_FRN, frn_rows)
            total_frn += len(frn_rows)
        if sub_rows:
            cursor.executemany(_INSERT_SUB, sub_rows)
            total_sub += len(sub_rows)
        if note_rows:
            cursor.executemany(_INSERT_NOTE, note_rows)
            total_note += len(note_rows)
        if xref_rows:
            cursor.executemany(_INSERT_XREF, xref_rows)
            total_xref += len(xref_rows)
        conn.commit()
        frn_rows.clear()
        sub_rows.clear()
        note_rows.clear()
        xref_rows.clear()

    try:
        for record in _iter_records(path):
            frn = record.get('frn')
            if not frn:
                continue

            frn_rows.append((
                frn,
                _get(record, 'make'),
                _get(record, 'model'),
                _get(record, 'manufacturer'),
                _get(record, 'level'),
                _get(record, 'type'),
                _get(record, 'action'),
                _get(record, 'country'),
                _get(record, 'legal_classification'),
                _get(record, 'serial_numbering'),
                _get(record, 'year_dates'),
                _get(record, 'importer'),
            ))

            for sub in record.get('sub_entries', []):
                sub_rows.append((
                    frn,
                    _get(sub, 'frn'),
                    _get(sub, 'calibre'),
                    _get(sub, 'shots'),
                    _get(sub, 'barrel_length'),
                    _get(sub, 'legal_classification'),
                ))

            for key, bullets in record.get('notes', {}).items():
                for idx, bullet in enumerate(bullets):
                    note_rows.append((frn, key, idx, bullet))

            for xref in record.get('cross_references', []):
                xref_rows.append((
                    frn,
                    _get(xref, 'frn'),
                    _get(xref, 'description'),
                ))

            if len(frn_rows) >= BATCH_SIZE:
                flush()
                elapsed = time.monotonic() - start_time
                rate = total_frn / elapsed if elapsed > 0 else 0
                sys.stdout.write(
                    f"\r  {total_frn:,} FRNs  "
                    f"{total_sub:,} sub-entries  "
                    f"{total_note:,} notes  "
                    f"{total_xref:,} cross-refs  "
                    f"({rate:.0f} rec/s)  "
                )
                sys.stdout.flush()

        flush()

    finally:
        conn.close()

    elapsed = time.monotonic() - start_time
    sys.stdout.write("\n")
    print("=" * 60)
    print(f"Done in {_format_duration(elapsed)}")
    print(f"  dbo_frn               {total_frn:>8,} rows")
    print(f"  dbo_frnSubEntry       {total_sub:>8,} rows")
    print(f"  dbo_frnNote           {total_note:>8,} rows")
    print(f"  dbo_frnCrossReference {total_xref:>8,} rows")


def main():
    parser = argparse.ArgumentParser(
        description="Import FRT JSON/JSONL output into a MySQL/MariaDB database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python importIntoDb.py output.jsonl --database FRT
  python importIntoDb.py output.jsonl --host myserver --database FRT --user root --password secret
  python importIntoDb.py output.jsonl --host myserver --port 3307 --database FRT --user admin --password secret
        """,
    )
    parser.add_argument("file",                              help="Path to the JSON or JSONL input file")
    parser.add_argument("--pages-dir", "-P", default=None,  help="Directory containing per-FRN PDFs to load into dbo_frnPdf")
    parser.add_argument("--host",     "-s", default="localhost", help="Database host (default: localhost)")
    parser.add_argument("--port",           type=int, default=3306, help="Database port (default: 3306)")
    parser.add_argument("--database", "-d", required=True,       help="Target database name")
    parser.add_argument("--user",     "-u", default="root",      help="Database user (default: root)")
    parser.add_argument("--password", "-p", default="",          help="Database password (default: empty)")

    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}")
        sys.exit(1)

    try:
        import_records(args.file, args.host, args.port, args.database, args.user, args.password)
        if args.pages_dir:
            if not os.path.isdir(args.pages_dir):
                print(f"Error: pages directory not found: {args.pages_dir}")
                sys.exit(1)
            print()
            import_pdfs(args.pages_dir, args.host, args.port, args.database, args.user, args.password)
    except MySQLError as exc:
        print(f"\nDatabase error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
