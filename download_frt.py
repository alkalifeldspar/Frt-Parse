#!/usr/bin/env python3
"""
Downloads the latest FRT PDF from the RCMP Firearms Reference Table page.
Usage: python download_frt.py [output_dir]
  output_dir defaults to the current directory.
"""

import re
import sys
from pathlib import Path

import urllib3
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PAGE_URL = "https://rcmp.ca/en/firearms/firearms-reference-table"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; frt-downloader/1.0)"}


def find_frt_pdf_url(page_url: str) -> tuple[str, str]:
    """Return (absolute_url, filename) for the frt-*.pdf linked on the page."""
    resp = requests.get(page_url, headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        filename = href.rsplit("/", 1)[-1]
        if re.match(r"frt-\d+\.pdf$", filename, re.IGNORECASE):
            if href.startswith("http"):
                return href, filename
            base = page_url.split("/en/")[0]
            return base + href, filename

    raise RuntimeError("No frt-*.pdf link found on the page.")


def download_pdf(url: str, dest: Path) -> None:
    print(f"Downloading {url} → {dest}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=120, verify=False) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {downloaded // (1 << 20)} MB / {total // (1 << 20)} MB ({pct}%)", end="", flush=True)
    print(f"\nDone: {dest}")


def main() -> None:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)

    url, filename = find_frt_pdf_url(PAGE_URL)
    dest = output_dir / filename

    if dest.exists():
        print(f"{dest} already exists — skipping download.")
        return

    download_pdf(url, dest)


if __name__ == "__main__":
    main()
