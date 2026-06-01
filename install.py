#!/usr/bin/env python3
"""Install required packages for the FRT parser and database scripts."""

import subprocess
import sys

PACKAGES = [
    "pdfplumber",              # parse_frt.py  — PDF text extraction
    "pypdf",                   # parse_frt.py  — FRN PDF page extraction
    "mysql-connector-python",  # createDb.py / importIntoDb.py — MySQL/MariaDB
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
