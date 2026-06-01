#!/usr/bin/env python3
"""
runAll.py — Full FRT pipeline: parse → split → create DB → import → cleanup.

Runs each step in sequence, uses temp files for intermediate output, and
deletes them on completion (or on failure).

Usage:
  python runAll.py frt-0504.pdf --database FRT
  python runAll.py frt-0504.pdf --host myserver --database FRT --user root --password secret
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _script(name: str) -> str:
    return os.path.join(_HERE, name)


def _run(label: str, cmd: list) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}\n")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nError: {label} failed (exit code {result.returncode})")
        sys.exit(result.returncode)


def _fmt(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full FRT pipeline: parse, split, create DB, import, cleanup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python runAll.py frt-0504.pdf --database FRT
  python runAll.py frt-0504.pdf --host myserver --database FRT --user root --password secret
  python runAll.py frt-0504.pdf --database FRT --workers 0
        """,
    )
    parser.add_argument("pdf",              help="Path to the FRT PDF")
    parser.add_argument("--host",     "-s", default="localhost",  help="Database host (default: localhost)")
    parser.add_argument("--port",           type=int, default=3306, help="Database port (default: 3306)")
    parser.add_argument("--database", "-d", required=True,        help="Target database name")
    parser.add_argument("--user",     "-u", default="root",       help="Database user (default: root)")
    parser.add_argument("--password", "-p", default="",           help="Database password (default: empty)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Worker processes for parse_frt.py (default: 1, 0=auto)")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"Error: PDF not found: {args.pdf}")
        sys.exit(1)

    db_flags = [
        "--host",     args.host,
        "--port",     str(args.port),
        "--database", args.database,
        "--user",     args.user,
        "--password", args.password,
    ]

    # Temp paths — always cleaned up in the finally block
    fd, out_json = tempfile.mkstemp(suffix=".json", prefix="frt_output_")
    os.close(fd)
    pages_dir = tempfile.mkdtemp(prefix="frt_pages_")

    start = time.monotonic()

    try:
        _run("Step 1/4 — parse_frt.py  (parse PDF → JSON)", [
            sys.executable, _script("parse_frt.py"),
            args.pdf, out_json,
            "-w", str(args.workers),
        ])

        _run("Step 2/4 — split_frn.py  (split PDF → per-FRN PDFs)", [
            sys.executable, _script("split_frn.py"),
            args.pdf, pages_dir,
            "-w", "2",
        ])

        _run("Step 3/4 — createDb.py  (create database tables)", [
            sys.executable, _script("createDb.py"),
            *db_flags,
        ])

        _run("Step 4/4 — importIntoDb.py  (load JSON + PDFs into DB)", [
            sys.executable, _script("importIntoDb.py"),
            out_json,
            *db_flags,
            "--pages-dir", pages_dir,
        ])

    finally:
        print("\nCleaning up temp files ...")
        if os.path.isfile(out_json):
            os.remove(out_json)
            print(f"  Removed {out_json}")
        if os.path.isdir(pages_dir):
            shutil.rmtree(pages_dir)
            print(f"  Removed {pages_dir}")

    print(f"\nAll done in {_fmt(time.monotonic() - start)}.")


if __name__ == "__main__":
    main()
