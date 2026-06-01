#!/usr/bin/env python3
"""
trim_pdf.py — Extract the first N pages of a PDF for testing.

Usage:
    python trim_pdf.py frt-0504.pdf
    python trim_pdf.py frt-0504.pdf -n 500
    python trim_pdf.py frt-0504.pdf -n 500 -o test.pdf
"""

import argparse
import os
import sys

try:
    import pypdf
except ImportError:
    print("Error: pypdf is required.  Install with:  pip install pypdf")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract the first N pages of a PDF.",
    )
    parser.add_argument("pdf",              help="Input PDF path")
    parser.add_argument("-n", "--pages", type=int, default=1000,
                        help="Number of pages to keep (default: 1000)")
    parser.add_argument("-o", "--output",   default=None,
                        help="Output path (default: <name>_trim<N>.pdf)")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"Error: file not found: {args.pdf}")
        sys.exit(1)

    if args.output:
        out_path = args.output
    else:
        base, ext = os.path.splitext(args.pdf)
        out_path = f"{base}_trim{args.pages}{ext}"

    reader = pypdf.PdfReader(args.pdf)
    total  = len(reader.pages)
    n      = min(args.pages, total)

    print(f"Input:  {args.pdf}  ({total:,} pages)")
    print(f"Output: {out_path}  ({n:,} pages)")

    writer = pypdf.PdfWriter()
    for i in range(n):
        writer.add_page(reader.pages[i])

    with open(out_path, "wb") as f:
        writer.write(f)

    print("Done.")


if __name__ == "__main__":
    main()
