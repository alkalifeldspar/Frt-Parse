#!/usr/bin/env python3
"""
FRT PDF Parser — converts the RCMP Firearms Reference Table PDF to JSON.

The FRT (frt-0504.pdf) contains 108,284 pages of Canadian firearm classification
data, one firearm record per page (some records span 2–3 pages).  Each page
follows a fixed layout:

    Firearm Reference Number (FRN): <number>
    Make / Model / Manufacturer / ...
    Calibre, Shots and Barrel Length   ← table of sub-entries (FRN-1, FRN-2, …)
    Notes
    Cross-References                   ← links to related FRN records
    Also Known As / Product Code

Output: a pretty-printed JSON array written to the output file, one object per
parent FRN.  Fields present only when the PDF contains them:

    {
      "frn":                  "166062",
      "make":                 "2 Vets Arms",
      "model":                "2VA-10",
      "manufacturer":         "2 Vets Arms",
      "level":                "Manufacturer Specifications and Commercial Customization",
      "type":                 "Rifle",
      "action":               "Semi-Automatic",
      "country":              "UNITED STATES OF AMERICA",
      "legal_classification": "Prohibited",
      "serial_numbering":     "...",
      "year_dates":           "...",
      "importer":             "...",
      "notes": {
        "Make":  ["make is marked on the receiver ..."],
        "Model": ["introduced in 2015.", "features include: ..."],
        "Canadian Law Comments": ["this receiver is a nearly complete firearm ..."]
      },
      "sub_entries": [
        {
          "frn":                  "166062-1",
          "calibre":              "308 WIN",
          "shots":                "5",
          "barrel_length":        "457",
          "legal_classification": "Prohibited"
        },
        ...
      ],
      "cross_references": [
        {"frn": "176685", "description": "Stealth Arms 1911 Government ..."}
      ]
    }

Usage:
    python parse_frt.py frt-0504.pdf output.json            # sequential
    python parse_frt.py frt-0504.pdf output.json -w 0       # parallel (auto workers)
    python parse_frt.py frt-0504.pdf output.json -w 8       # parallel (8 workers)
"""

import os
import pdfplumber
import json
import re
import sys
import time
import argparse
from typing import Dict
from datetime import datetime


def _to_camel_case(s: str) -> str:
    """Convert a space-separated label to camelCase ('Serial Number' → 'serialNumber')."""
    words = s.split()
    return words[0].lower() + ''.join(w.capitalize() for w in words[1:])


def _format_duration(seconds: float) -> str:
    """Format a duration as h:mm:ss or m:ss."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

class FRTParser:
    """Parse an FRT PDF and write one JSON record per parent FRN.

    Two entry points are provided:
      - parse()          — single process, streams page-by-page.
      - parse_parallel() — splits the PDF across N worker processes then
                           merges results in document order.

    Both methods reopen the PDF file every BATCH_SIZE pages so that
    pdfminer's internal resource manager (font caches, CMap tables) is torn
    down and garbage-collected, keeping memory use bounded across 100k+ pages.
    """

    def __init__(self, pdf_path: str, output_path: str = 'frt_data.json'):
        """
        Args:
            pdf_path:    Path to the source FRT PDF.
            output_path: Destination for the JSON output (default: frt_data.json).
        """
        self.pdf_path = pdf_path
        self.output_path = output_path
        self.current_parent_frn = None
        self.current_record = {}
        self.sub_entries = []
        self.current_cross_refs = []
        self.records_processed = 0
        self.pages_processed = 0
        self.first_record = True
        
    @staticmethod
    def extract_frn(text: str) -> str:
        """Return the FRN found on this page, or None if not present.

        Sub-entry pages (e.g. "FRN: 166062 - 1") return "166062-1".
        Parent pages return the bare number string "166062".
        """
        match = re.search(r'Firearm Reference Number \(FRN\):\s*(\d+)\s*-\s*(\d+)', text)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        match = re.search(r'Firearm Reference Number \(FRN\):\s*(\d+)', text)
        return match.group(1) if match else None

    @staticmethod
    def is_sub_entry(frn: str) -> bool:
        """Return True if the FRN is a sub-entry (e.g. "166062-1")."""
        return '-' in frn if frn else False

    @staticmethod
    def extract_field_value(text: str) -> Dict[str, str]:
        """Scrape the labelled fields from a single page of text.

        Fields extracted: frn, make, model, manufacturer, level, type, action,
        country, legal_classification, serial_numbering, year_dates, importer.

        When a field value reads "See Note", the Notes section is searched for
        a line containing a relevant keyword and that line's value is used instead.
        Fields absent from the page or whose value is "No Data" are omitted.
        """
        data = {}
        
        # First, extract notes section if it exists
        notes_section = FRTParser._extract_notes(text)
        
        # Define patterns for key fields (excluding calibre/shots/barrel - those come from table)
        patterns = {
            'frn': r'Firearm Reference Number \(FRN\):\s*(\d+)',
            'make': r'Make:\s*([^\n]+?)(?:\n|$)',
            'model': r'Model:\s*([^\n]+?)(?:\n|$)',
            'manufacturer': r'Manufacturer:\s*([^\n]+?)(?:\n|$)',
            'level': r'Level:\s*([^\n]+?)(?:\n|$)',
            'type': r'Type:\s*([^\n]+?)(?:\n|$)',
            'action': r'Action:\s*([^\n]+?)(?:\n|$)',
            'country': r'Country of Manufacturer:\s*([^\n]+?)(?:\n|$)',
            'legal_classification': r'Legal Classification:\s*([^\n]+?)(?:\n|$)',
            'serial_numbering': r'Serial Numbering:\s*([^\n]+?)(?:\n|$)',
            'year_dates': r'Year Dates:\s*([^\n]+?)(?:\n|$)',
            'importer': r'Importer:\s*([^\n]+?)(?:\n|$)',
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1).strip() if match.lastindex else ''
                
                # If value says "See Note", try to get data from notes section
                if value and value.lower() == 'see note' and notes_section:
                    note_value = FRTParser._extract_from_notes(key, notes_section)
                    if note_value:
                        data[key] = note_value
                elif value and value.lower() not in ['no data', '']:
                    # Clean up the value - remove extra whitespace
                    value = ' '.join(value.split())
                    if value and value.lower() != 'no data':
                        data[key] = value
        
        return data
    
    @staticmethod
    def _extract_notes(text: str) -> str:
        """Return the raw text of the Notes section, or an empty string.

        Used internally to resolve "See Note" field values; for structured
        output use _parse_notes_section instead.
        """
        match = re.search(r'Notes\s*\n(.*?)(?:\n\n|\Z)', text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ''

    _NOTES_HEADER_RE = re.compile(r'^([A-Z][A-Za-z ]{1,49}?)\s*-{1,2}\s+(.+)$')

    @staticmethod
    def _parse_notes_section(text: str) -> dict:
        """Parse the Notes section into a structured dict of sub-sections.

        Each sub-section begins with a capitalised keyword followed by " - "
        (e.g. "Make - ...", "Model - ...", "Canadian Law Comments - ...").
        Continuation bullets start with "- ".  Word-wrapped continuation lines
        (no leading "- " and no new keyword) are appended to the previous bullet.

        Returns a dict mapping each sub-section name to a list of bullet strings:
            {
              "Make":  ["make is marked on the receiver ..."],
              "Model": ["introduced in 2015.", "features include: ..."],
              ...
            }
        Returns an empty dict when the Notes section is absent or contains no
        structured content.
        """
        m = re.search(r'Notes\s*\n', text, re.IGNORECASE)
        if not m:
            return {}

        section = text[m.end():]
        end = re.search(r'(?:Cross-References|Also Known As|Firearms Reference Table)', section)
        if end:
            section = section[:end.start()]

        section = section.strip()
        if not section or section.lower() in ('no data', 'n/a'):
            return {}

        notes = {}
        current_key = None
        current_bullets = []

        for line in section.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue

            header = FRTParser._NOTES_HEADER_RE.match(stripped)
            if header:
                if current_key is not None:
                    notes[current_key] = current_bullets
                current_key = _to_camel_case(header.group(1).strip())
                first_value = header.group(2).strip()
                current_bullets = [first_value] if first_value else []
            elif stripped.startswith('- '):
                bullet = stripped[2:].strip()
                if current_key is not None and bullet:
                    current_bullets.append(bullet)
            else:
                # Word-wrapped continuation — append to the previous bullet
                if current_key is not None and current_bullets:
                    current_bullets[-1] = current_bullets[-1] + ' ' + stripped

        if current_key is not None and current_bullets:
            notes[current_key] = current_bullets

        return notes
    
    @staticmethod
    def _extract_from_notes(field_name: str, notes_text: str) -> str:
        """Search notes_text for a line related to field_name and return its value.

        Used to resolve "See Note" field values.  Returns an empty string when
        no relevant line is found.
        """
        # Map field names to potential note keywords
        field_keywords = {
            'serial_numbering': ['serial', 'numbering'],
            'year_dates': ['year', 'date'],
            'calibre': ['calibre', 'caliber'],
            'barrel_length': ['barrel'],
            'country': ['country', 'manufacturer'],
        }
        
        keywords = field_keywords.get(field_name, [field_name])
        
        # Search for lines in notes that contain relevant keywords
        lines = notes_text.split('\n')
        for line in lines:
            line_lower = line.lower()
            for keyword in keywords:
                if keyword.lower() in line_lower:
                    # Extract the value part (after colon or on same line)
                    if ':' in line:
                        value = line.split(':', 1)[1].strip()
                    else:
                        value = line.strip()
                    
                    if value and value.lower() not in ['see note', 'no data', '']:
                        return value
        
        return ''

    @staticmethod
    def _extract_cross_references(text: str) -> list:
        """Extract cross-referenced FRNs from the Cross-References section.

        Each row in the section starts with a FRN number followed by make/model/etc.
        Returns a list of dicts: [{'frn': '176685', 'description': 'Stealth Arms ...'}, ...]
        """
        m = re.search(r'Cross-References\s*\n', text, re.IGNORECASE)
        if not m:
            return []

        section = text[m.end():]

        # Trim at the next major section or footer
        end = re.search(r'(?:Also Known As|Firearms Reference Table)', section)
        if end:
            section = section[:end.start()]

        cross_refs = []
        for line in section.split('\n'):
            line = line.strip()
            # Data rows start with a bare FRN number
            row = re.match(r'^(\d+)\s+(.+)$', line)
            if row:
                cross_refs.append({'frn': row.group(1), 'description': row.group(2).strip()})

        return cross_refs

    def parse(self):
        """Parse the PDF sequentially and write the JSON output file.

        Pages are processed in batches of BATCH_SIZE.  The PDF is closed and
        reopened between batches so pdfminer's font/CMap caches are freed,
        keeping memory use flat regardless of total page count.
        """
        print(f"Starting FRT PDF parsing...")
        print(f"Input: {self.pdf_path}")
        print(f"Output: {self.output_path}")
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        # Reopen the PDF every BATCH_SIZE pages so pdfminer's resource manager
        # (font caches, CMap data, colour spaces) is torn down and GC'd.
        BATCH_SIZE = 1000

        try:
            import gc

            with pdfplumber.open(self.pdf_path) as _pdf:
                total_pages = len(_pdf.pages)

            w = len(str(total_pages))  # digit width for alignment
            start_time = time.monotonic()

            with open(self.output_path, 'w', encoding='utf-8') as output_file:
                output_file.write('[\n')

                for batch_start in range(0, total_pages, BATCH_SIZE):
                    batch_end = min(batch_start + BATCH_SIZE, total_pages)

                    with pdfplumber.open(self.pdf_path) as pdf:
                        for page_idx in range(batch_start, batch_end):
                            text = pdf.pages[page_idx].extract_text()
                            page_num = page_idx + 1

                            if text:
                                frn = self.extract_frn(text)

                                if frn and not self.is_sub_entry(frn):
                                    if frn != self.current_parent_frn:
                                        if self.current_record and self.current_parent_frn:
                                            self._write_record_with_subentries(output_file)
                                        self.current_parent_frn = frn
                                        self.current_record = {'frn': frn}
                                        self.sub_entries = []
                                        self.current_cross_refs = []

                                    fields = self.extract_field_value(text)
                                    self.current_record.update(fields)

                                    if 'Notes' in text and 'notes' not in self.current_record:
                                        notes_dict = self._parse_notes_section(text)
                                        if notes_dict:
                                            self.current_record['notes'] = notes_dict

                                    if 'Calibre' in text and 'Shots' in text and 'Barrel' in text and not self.sub_entries:
                                        self.sub_entries.extend(self._parse_calibre_table_from_text(text))

                                    if 'Cross-References' in text and not self.current_cross_refs:
                                        self.current_cross_refs = self._extract_cross_references(text)

                            self.pages_processed += 1

                            elapsed = time.monotonic() - start_time
                            rate = self.pages_processed / elapsed if elapsed > 0 else 0
                            eta = (total_pages - page_num) / rate if rate > 0 else 0
                            sys.stdout.write(
                                f"\r  {page_num:{w},}/{total_pages:,}"
                                f"  ({100 * page_num / total_pages:5.1f}%)"
                                f"  records: {self.records_processed:,}"
                                f"  {rate:,.0f} pg/s"
                                f"  ETA {_format_duration(eta)}  "
                            )
                            sys.stdout.flush()

                    # PDF closed here — pdfminer caches freed
                    gc.collect()

                if self.current_record and self.current_parent_frn:
                    self._write_record_with_subentries(output_file)
                output_file.write('\n]\n')

            elapsed = time.monotonic() - start_time
            sys.stdout.write('\n')
            print("=" * 80)
            print(f"Parsing complete!")
            print(f"Pages processed:   {self.pages_processed:,}")
            print(f"Records extracted: {self.records_processed:,}")
            print(f"Elapsed:           {_format_duration(elapsed)}")
            print(f"Output file:       {self.output_path}")

        except Exception as e:
            print(f"Error during parsing: {e}")
            import traceback
            traceback.print_exc()
    
    def _write_record_with_subentries(self, file_handle):
        """Serialise the current record to file_handle as pretty-printed JSON.

        Attaches sub_entries and cross_references to the record dict before
        serialising, then resets first_record so subsequent records are
        comma-separated within the JSON array.
        """
        try:
            if self.sub_entries:
                self.current_record['sub_entries'] = self.sub_entries
            if self.current_cross_refs:
                self.current_record['cross_references'] = self.current_cross_refs

            json_block = json.dumps(self.current_record, ensure_ascii=False, indent=2)
            if not self.first_record:
                file_handle.write(',\n')
            file_handle.write(json_block)
            self.first_record = False

            self.records_processed += 1

        except Exception as e:
            print(f"Error writing record {self.current_parent_frn}: {e}")

    def parse_parallel(self, num_workers: int = None):
        """Parse the PDF using multiple worker processes and write the JSON output file.

        Strategy (map-reduce):
          1. Split the PDF into num_workers equal page-range chunks.
          2. Each worker opens its own PDF handle, processes its chunk in
             sub-batches of 500 pages (to bound pdfminer memory per worker),
             and returns a list of per-page result tuples.
          3. The main process sorts all results by page index (restoring document
             order), merges pages that share an FRN, and writes the output.

        Args:
            num_workers: Number of worker processes.  None (default) uses
                         cpu_count() - 1.  Pass 0 from the CLI for the same.
        """
        import threading
        from multiprocessing import Pool, cpu_count, Queue

        if num_workers is None:
            num_workers = max(1, cpu_count() - 1)

        print(f"Starting parallel FRT PDF parsing ({num_workers} workers)...")
        print(f"Input:  {self.pdf_path}")
        print(f"Output: {self.output_path}")
        print(f"Start:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)

            chunk_size = max(1, (total_pages + num_workers - 1) // num_workers)
            progress_q = Queue()
            chunks = [
                (self.pdf_path, i * chunk_size, min((i + 1) * chunk_size, total_pages), i)
                for i in range(num_workers)
                if i * chunk_size < total_pages
            ]
            print(f"Total pages: {total_pages:,}  |  Workers: {num_workers}  |  ~{chunk_size:,} pages each")

            start_time = time.monotonic()
            frns_found = [0]   # written only by progress thread; read by main after join
            stop_flag = threading.Event()

            def _progress_thread():
                import queue as _q
                while not stop_flag.is_set():
                    try:
                        while True:
                            progress_q.get_nowait()
                            frns_found[0] += 1
                    except _q.Empty:
                        pass
                    elapsed = time.monotonic() - start_time
                    rate = frns_found[0] / elapsed if elapsed > 0 else 0
                    pct = 100 * frns_found[0] / total_pages
                    sys.stdout.write(
                        f"\r  FRNs parsed: {frns_found[0]:,}"
                        f"  (~{pct:.1f}%)"
                        f"  {rate:.0f} FRN/s"
                        f"  elapsed {_format_duration(elapsed)}  "
                    )
                    sys.stdout.flush()
                    stop_flag.wait(timeout=0.25)

            t = threading.Thread(target=_progress_thread, daemon=True)
            t.start()

            all_results = []
            with Pool(num_workers, initializer=_init_worker, initargs=(progress_q,)) as pool:
                for chunk_result in pool.imap_unordered(_worker_extract_pages, chunks):
                    all_results.extend(chunk_result)

            stop_flag.set()
            t.join(timeout=2)

            # Drain any items the thread didn't consume before stopping
            import queue as _q
            try:
                while True:
                    progress_q.get_nowait()
                    frns_found[0] += 1
            except _q.Empty:
                pass

            elapsed = time.monotonic() - start_time
            sys.stdout.write(
                f"\r  FRNs parsed: {frns_found[0]:,}"
                f"  (100.0%)"
                f"  elapsed {_format_duration(elapsed)}        \n"
            )
            sys.stdout.flush()

            # Restore document order, then assemble records by FRN
            sys.stdout.write("  Assembling records...")
            sys.stdout.flush()
            all_results.sort(key=lambda x: x[0])

            records = {}    # frn -> {'data': {...}, 'sub_entries': [...], 'cross_refs': [...]}
            frn_order = []  # insertion order = document order

            for _, frn, fields, sub_entries, cross_refs, notes_dict in all_results:
                if frn not in records:
                    records[frn] = {'data': {'frn': frn}, 'sub_entries': [], 'cross_refs': []}
                    frn_order.append(frn)
                records[frn]['data'].update(fields)
                if notes_dict and 'notes' not in records[frn]['data']:
                    records[frn]['data']['notes'] = notes_dict
                if sub_entries and not records[frn]['sub_entries']:
                    records[frn]['sub_entries'] = sub_entries
                if cross_refs and not records[frn]['cross_refs']:
                    records[frn]['cross_refs'] = cross_refs

            sys.stdout.write(f" {len(frn_order):,} records\n")
            sys.stdout.write("  Writing output...")
            sys.stdout.flush()

            with open(self.output_path, 'w', encoding='utf-8') as f:
                f.write('[\n')
                first = True
                for frn in frn_order:
                    rec = records[frn]['data']
                    subs = records[frn]['sub_entries']
                    if subs:
                        rec['sub_entries'] = subs
                    if records[frn]['cross_refs']:
                        rec['cross_references'] = records[frn]['cross_refs']
                    if not first:
                        f.write(',\n')
                    f.write(json.dumps(rec, ensure_ascii=False, indent=2))
                    first = False
                    self.records_processed += 1
                f.write('\n]\n')

            elapsed = time.monotonic() - start_time
            sys.stdout.write(" done\n")
            print("=" * 80)
            print(f"Parsing complete!")
            print(f"Records extracted: {self.records_processed:,}")
            print(f"Elapsed:           {_format_duration(elapsed)}")
            print(f"Output file:       {self.output_path}")

        except Exception as e:
            print(f"Error during parallel parsing: {e}")
            import traceback
            traceback.print_exc()

    # Single-token classification values.
    _SINGLE_CLASSIFICATIONS = frozenset({'Restricted', 'Prohibited', 'Non-Restricted', 'Antique'})
    # Tokens that mark the start of the Level / Legal Authority text after a CC classification.
    _CLASSIFICATION_STOP = frozenset({'para.', 'Non-Commercial', 'Manufacturer'})

    @staticmethod
    def _parse_calibre_table_from_text(text: str) -> list:
        """Parse the "Calibre, Shots and Barrel Length" table into sub-entry dicts.

        Column order: calibre  shots  barrel_mm  classification  [legal authority  level]

        Shots and barrel are always bare integers.  Calibre may be empty, a bare
        integer ("38", "44"), or a number+word combo ("308 WIN", "44 COLT").
        Classification may be a single word ("Restricted", "Antique") or a multi-token
        CC reference ('CC 2 "firearm"', 'CC 84(3) Exempted').

        The parser locates shots and barrel by finding the rightmost pair of adjacent
        digit-only tokens, which is independent of calibre format and classification
        text.  Everything to the left of that pair is calibre; everything to the
        right is the classification.

        Returns a list of dicts with keys: frn, calibre, shots, barrel_length,
        legal_classification (all strings; absent when not parseable).
        """
        sub_entries = []
        lines = text.split('\n')

        in_calibre_section = False
        for line in lines:
            line_stripped = line.strip()

            if 'Calibre' in line and 'Shots' in line and 'Barrel' in line:
                in_calibre_section = True
                continue

            if in_calibre_section and any(keyword in line_stripped for keyword in ['Notes', 'Cross-References', 'Also Known', 'Year Dates', 'Importer']):
                in_calibre_section = False
                continue

            if in_calibre_section and line_stripped:
                match = re.match(r'(\d+\s*-\s*\d+)\s+(.+)', line_stripped)
                if match:
                    frn_str = match.group(1).replace(' ', '')
                    parts = match.group(2).split()

                    if len(parts) < 2:
                        continue

                    sub_entry = {'frn': frn_str}

                    # Find the rightmost pair of adjacent digit-only tokens.
                    # Those positions are shots and barrel_length; everything
                    # before them is calibre; everything after is classification.
                    digit_positions = [k for k, p in enumerate(parts) if p.isdigit()]
                    shots_idx = barrel_idx = None
                    for i in range(len(digit_positions) - 1, 0, -1):
                        if digit_positions[i] == digit_positions[i - 1] + 1:
                            shots_idx = digit_positions[i - 1]
                            barrel_idx = digit_positions[i]
                            break

                    if shots_idx is None:
                        continue  # malformed row — skip

                    sub_entry['shots'] = parts[shots_idx]
                    sub_entry['barrel_length'] = parts[barrel_idx]

                    calibre_parts = parts[:shots_idx]
                    if calibre_parts:
                        sub_entry['calibre'] = ' '.join(calibre_parts)

                    # Extract classification from tokens immediately after barrel.
                    cls_start = barrel_idx + 1
                    if cls_start < len(parts):
                        cls_token = parts[cls_start]
                        if cls_token in FRTParser._SINGLE_CLASSIFICATIONS:
                            sub_entry['legal_classification'] = cls_token
                        elif cls_token == 'CC':
                            # Multi-token CC reference: collect tokens until a
                            # Level/Authority stop word or the 6-token limit.
                            cls_parts = []
                            i = cls_start
                            while i < len(parts) and len(cls_parts) < 6:
                                if parts[i] in FRTParser._CLASSIFICATION_STOP:
                                    break
                                cls_parts.append(parts[i])
                                i += 1
                            sub_entry['legal_classification'] = ' '.join(cls_parts)
                        else:
                            sub_entry['legal_classification'] = cls_token

                    if sub_entry.get('calibre') or sub_entry.get('shots') or sub_entry.get('barrel_length'):
                        sub_entries.append(sub_entry)

        return sub_entries
    
_worker_progress_q = None  # set in each worker by _init_worker via Pool initializer


def _init_worker(q):
    """Pool initializer: store the shared progress queue in the worker's global."""
    global _worker_progress_q
    _worker_progress_q = q


def _worker_extract_pages(args):
    """Multiprocessing worker that extracts data from a contiguous page range.

    Args (unpacked from a single tuple for Pool.imap compatibility):
        pdf_path      (str): Path to the PDF file.
        start_page    (int): First page index (inclusive).
        end_page      (int): Last page index (exclusive).
        worker_id     (int): Used in error messages only.

    The progress queue is NOT in the tuple; it is stored in the module-level
    _worker_progress_q global by the Pool initializer (_init_worker) so that
    the Queue is shared via process inheritance rather than pickle.

    Returns:
        List of tuples: (page_idx, frn, fields, sub_entries, cross_refs, notes_dict)
        where fields is the dict from extract_field_value, sub_entries and
        cross_refs are lists (empty when not present on that page).
    """
    pdf_path, start_page, end_page, worker_id = args
    import gc

    BATCH_SIZE = 500  # smaller than sequential — N workers run concurrently
    results = []
    try:
        for batch_start in range(start_page, end_page, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, end_page)
            with pdfplumber.open(pdf_path) as pdf:
                total = len(pdf.pages)
                for page_idx in range(batch_start, min(batch_end, total)):
                    text = pdf.pages[page_idx].extract_text()
                    if not text:
                        continue
                    frn = FRTParser.extract_frn(text)
                    if not frn or FRTParser.is_sub_entry(frn):
                        continue
                    fields = FRTParser.extract_field_value(text)
                    sub_entries = []
                    if 'Calibre' in text and 'Shots' in text and 'Barrel' in text:
                        sub_entries = FRTParser._parse_calibre_table_from_text(text)
                    cross_refs = []
                    if 'Cross-References' in text:
                        cross_refs = FRTParser._extract_cross_references(text)
                    notes_dict = {}
                    if 'Notes' in text:
                        notes_dict = FRTParser._parse_notes_section(text)
                    results.append((page_idx, frn, fields, sub_entries, cross_refs, notes_dict))
                    if _worker_progress_q is not None:
                        _worker_progress_q.put(1)
            # PDF closed here — pdfminer caches freed
            gc.collect()
    except Exception as e:
        import traceback
        print(f"Worker {worker_id} error (pages {start_page}-{end_page}): {e}", flush=True)
        traceback.print_exc()
    return results


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Parse FRT PDF and convert to JSON format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python parse_frt.py frt-0504.pdf frt_data.json
  python parse_frt.py /path/to/input.pdf /path/to/output.json
  python parse_frt.py frt-0504.pdf output.json --verbose
        '''
    )
    
    parser.add_argument(
        'pdf_path',
        help='Path to the input PDF file'
    )
    parser.add_argument(
        'output_path',
        nargs='?',
        default='frt_data.json',
        help='Path to the output JSON file (default: frt_data.json)'
    )
    
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=1,
        metavar='N',
        help='Number of parallel worker processes (default: 1). '
             'Use 0 to auto-detect (cpu_count - 1).'
    )
    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        print(f"Error: PDF file not found: {args.pdf_path}")
        return

    parser_instance = FRTParser(args.pdf_path, args.output_path)
    if args.workers != 1:
        parser_instance.parse_parallel(None if args.workers == 0 else args.workers)
    else:
        parser_instance.parse()

if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    main()
