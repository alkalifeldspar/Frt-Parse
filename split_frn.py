#!/usr/bin/env python3
"""
split_frn.py — Split a FRT PDF into per-FRN PDFs.

Scans every page for "Firearm Reference Number (FRN): <number>" near the top.
Pages that carry a FRN are written to <pages_dir>/<frn>.pdf.
Sub-entry pages (FRN: 12345-1) are grouped under the parent FRN (12345.pdf).
Pages with no FRN at the top are skipped.

Scan and write run concurrently: as the scanner detects FRN transitions it
enqueues completed FRNs; writer threads flush them to disk in parallel.

Usage:
    python split_frn.py frt-0504.pdf frn_pages/
    python split_frn.py frt-0504.pdf frn_pages/ -w 4 --top-chars 400
"""

import argparse
import os
import queue
import re
import sys
import threading
import time

try:
    import pdfplumber
except ImportError:
    print("Error: pdfplumber is required.  Install with:  pip install pdfplumber")
    sys.exit(1)

try:
    import pypdf
except ImportError:
    print("Error: pypdf is required.  Install with:  pip install pypdf")
    sys.exit(1)


_FRN_SUB    = re.compile(r'Firearm Reference Number \(FRN\):\s*(\d+)\s*-\s*(\d+)')
_FRN_PARENT = re.compile(r'Firearm Reference Number \(FRN\):\s*(\d+)')

# Reopen pdfplumber every N pages so its per-page layout cache doesn't grow unbounded.
_SCAN_BATCH = 5000


def _extract_frn(text: str) -> str | None:
    """Return the parent FRN from page text, or None if not present."""
    m = _FRN_SUB.search(text)
    if m:
        return m.group(1)
    m = _FRN_PARENT.search(text)
    return m.group(1) if m else None


def _fmt(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _writer(pdf_path: str, pages_dir: str, q: queue.Queue, counter: list, lock: threading.Lock, errors: list) -> None:
    """Pull (frn, indices) from queue, write one PDF per item."""
    reader = pypdf.PdfReader(pdf_path)
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            break
        frn, indices = item
        try:
            writer = pypdf.PdfWriter()
            for idx in indices:
                writer.add_page(reader.pages[idx])
            with open(os.path.join(pages_dir, f"{frn}.pdf"), 'wb') as f:
                writer.write(f)
            with lock:
                counter[0] += 1
        except Exception as exc:
            errors.append(f"{frn}: {exc}")
        finally:
            q.task_done()


def split(pdf_path: str, pages_dir: str, top_chars: int = 400, num_writers: int = 4) -> None:
    os.makedirs(pages_dir, exist_ok=True)

    # Get page count cheaply (page tree traversal only, no content loaded)
    with open(pdf_path, 'rb') as f:
        total = len(pypdf.PdfReader(f).pages)

    q       = queue.Queue(maxsize=num_writers * 4)  # backpressure so scanner doesn't outrun writers
    counter = [0]
    lock    = threading.Lock()
    errors  = []

    threads = []
    for _ in range(num_writers):
        t = threading.Thread(
            target=_writer,
            args=(pdf_path, pages_dir, q, counter, lock, errors),
            daemon=True,
        )
        t.start()
        threads.append(t)

    print(f"Scanning {os.path.basename(pdf_path)} with {num_writers} writer thread(s) ...")
    start = time.monotonic()

    current_frn     = None
    current_indices = []
    n_found         = 0
    w               = len(str(total))

    def enqueue(frn, indices):
        nonlocal n_found
        n_found += 1
        q.put((frn, sorted(indices)))   # blocks when queue is full (natural backpressure)

    # Process in batches: exiting the `with` block releases pdfplumber's per-page
    # layout cache so memory stays bounded regardless of total page count.
    for batch_start in range(0, total, _SCAN_BATCH):
        batch_end = min(batch_start + _SCAN_BATCH, total)
        with pdfplumber.open(pdf_path) as pdf:
            for i in range(batch_start, batch_end):
                text = (pdf.pages[i].extract_text() or '')[:top_chars]
                frn  = _extract_frn(text)

                if frn != current_frn:
                    if current_frn is not None:
                        enqueue(current_frn, current_indices)
                    current_frn     = frn
                    current_indices = [i] if frn else []
                elif frn:
                    current_indices.append(i)

                pages_done = i + 1
                if pages_done % 10 == 0 or pages_done == total:
                    elapsed = time.monotonic() - start
                    rate    = pages_done / elapsed if elapsed > 0 else 0
                    sys.stdout.write(
                        f"\r  {pages_done:{w},}/{total:,}"
                        f"  found: {n_found:,}"
                        f"  written: {counter[0]:,}"
                        f"  {rate:,.0f} pg/s"
                    )
                    sys.stdout.flush()

    if current_frn is not None:
        enqueue(current_frn, current_indices)

    # Signal writers to stop and wait for all queued work to finish
    for _ in threads:
        q.put(None)
    q.join()

    sys.stdout.write("\n")
    elapsed = time.monotonic() - start
    print(f"Done in {_fmt(elapsed)}  —  {counter[0]:,} PDFs written to {pages_dir}/")

    if errors:
        print(f"\n{len(errors)} write error(s):")
        for e in errors[:20]:
            print(f"  {e}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a FRT PDF into one PDF per FRN.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python split_frn.py frt-0504.pdf frn_pages/
  python split_frn.py frt-0504.pdf frn_pages/ -w 4 --top-chars 300
        """,
    )
    parser.add_argument("pdf",       help="Path to the FRT PDF")
    parser.add_argument("pages_dir", help="Output directory for per-FRN PDFs")
    parser.add_argument("-w", "--writers", type=int, default=4,
                        help="Number of writer threads (default: 4)")
    parser.add_argument("--top-chars", type=int, default=400,
                        help="Characters from page top to search for FRN (default: 400)")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"Error: file not found: {args.pdf}")
        sys.exit(1)

    split(args.pdf, args.pages_dir, args.top_chars, args.writers)


if __name__ == "__main__":
    main()
