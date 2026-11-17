#!/usr/bin/env python3
"""Install required packages for the FRT parser and database scripts."""

import subprocess
import sys

PACKAGES = [
    "pypdf",                   # split_frn.py  — FRN PDF page extraction
    "pypdfium2",               # parse_frt.py / split_frn.py — fast page text extraction
    "mysql-connector-python",  # createDb.py / importIntoDb.py — MySQL/MariaDB
    "requests",                # download_frt.py — HTTP download
    "beautifulsoup4",          # download_frt.py — HTML parsing
]

def main():
    print(f"Installing packages using {sys.executable}\n")
    failed = []
    for package in PACKAGES:
        print(f"  {package} ...", end=" ", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("ok")
        else:
            print("FAILED")
            failed.append((package, result.stderr.strip()))

    print()
    if failed:
        print("The following packages failed to install:")
        for package, err in failed:
            print(f"  {package}: {err}")
        sys.exit(1)
    else:
        print("All packages installed successfully.")

if __name__ == "__main__":
    main()
