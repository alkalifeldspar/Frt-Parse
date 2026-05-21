#!/usr/bin/env python3
"""
FRT PDF Parser - Converts frt-0504.pdf to JSON format
Processes 108,284 pages of Canadian firearm classification data.

Output format: JSONL (JSON Lines) - one firearm record per line
"""

import pdfplumber
import json
import re
import sys
import argparse
from typing import Dict
from datetime import datetime

class FRTParser:
    def __init__(self, pdf_path: str, output_path: str = 'frt_data.jsonl'):
        self.pdf_path = pdf_path
        self.output_path = output_path
        self.current_parent_frn = None
        self.current_record = {}  # Parent record
        self.sub_entries = []  # Array of sub-entries for current parent
        self.records_processed = 0
        self.pages_processed = 0
        
    def extract_frn(self, text: str) -> str:
        """Extract Firearm Reference Number from text (including sub-entries like 166062-1)"""
        # First check for sub-entries like "166062 - 1" or "166062-1"
        match = re.search(r'Firearm Reference Number \(FRN\):\s*(\d+)\s*-\s*(\d+)', text)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        
        # Otherwise get main FRN
        match = re.search(r'Firearm Reference Number \(FRN\):\s*(\d+)', text)
        return match.group(1) if match else None
    
    def is_sub_entry(self, frn: str) -> bool:
        """Check if FRN is a sub-entry (contains dash)"""
        return '-' in frn if frn else False
    
    def get_parent_frn(self, frn: str) -> str:
        """Get parent FRN from a sub-entry"""
        return frn.split('-')[0] if self.is_sub_entry(frn) else frn
    
    def extract_field_value(self, text: str) -> Dict[str, str]:
        """Extract key-value pairs from FRT report text"""
        data = {}
        
        # First, extract notes section if it exists
        notes_section = self._extract_notes(text)
        
        # Define patterns for key fields
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
                    note_value = self._extract_from_notes(key, notes_section)
                    if note_value:
                        data[key] = note_value
                elif value and value.lower() not in ['no data', '']:
                    # Clean up the value - remove extra whitespace
                    value = ' '.join(value.split())
                    if value and value.lower() != 'no data':
                        data[key] = value
        
        # Special handling for Calibre (looks for "Calibre -" or detailed calibre info)
        calibre_match = re.search(r'Calibre\s*-\s*(.+?)(?:\n(?:Shots|Cross-References|Serial|Also Known|Year|Legal|Notes)|\Z)', text, re.IGNORECASE | re.DOTALL)
        if calibre_match:
            calibre_text = calibre_match.group(1).strip()
            # Take first line or first 100 chars if multi-line
            calibre_value = calibre_text.split('\n')[0].strip()
            if calibre_value and calibre_value.lower() not in ['no data', '']:
                data['calibre'] = calibre_value
        
        # Special handling for Shots (looks for "Shots -" or detailed shots info)
        shots_match = re.search(r'Shots\s*-\s*(.+?)(?:\n(?:Cross-References|Serial|Also Known|Legal|Notes|Importer|Year)|\Z)', text, re.IGNORECASE | re.DOTALL)
        if shots_match:
            shots_text = shots_match.group(1).strip()
            # Take first line or first 100 chars if multi-line
            shots_value = shots_text.split('\n')[0].strip()
            if shots_value and shots_value.lower() not in ['no data', '']:
                data['shots'] = shots_value
        
        # Special handling for Barrel Length (can be in a table or listed under Calibre section)
        barrel_match = re.search(r'Barrel\s+(?:Length|Len\.?)\s*(?:\(mm\))?\s*[,:]?\s*(\d+.*?)(?:\n|$)', text, re.IGNORECASE)
        if barrel_match:
            barrel_value = barrel_match.group(1).strip()
            if barrel_value and barrel_value.lower() not in ['no data', '']:
                data['barrel_length'] = barrel_value
        
        return data
    
    def _extract_notes(self, text: str) -> str:
        """Extract the Notes section from the text"""
        match = re.search(r'Notes\s*\n(.*?)(?:\n\n|\Z)', text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ''
    
    def _extract_from_notes(self, field_name: str, notes_text: str) -> str:
        """Extract specific field data from notes section"""
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
    
    def parse(self):
        """Main parsing function - processes all pages and writes JSON output"""
        print(f"Starting FRT PDF parsing...")
        print(f"Input: {self.pdf_path}")
        print(f"Output: {self.output_path}")
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)
                
                with open(self.output_path, 'w', encoding='utf-8') as output_file:
                    # Process all pages
                    for page_num, page in enumerate(pdf.pages, 1):
                        text = page.extract_text()
                        
                        if text:
                            # Extract FRN to identify new records
                            frn = self.extract_frn(text)
                            
                            if frn:
                                is_sub = self.is_sub_entry(frn)
                                parent_frn = self.get_parent_frn(frn)
                                
                                if is_sub:
                                    # This is a sub-entry
                                    # Check if it belongs to current parent
                                    if parent_frn != self.current_parent_frn:
                                        # Parent FRN changed, write previous parent record
                                        if self.current_record and self.current_parent_frn:
                                            self._write_record_with_subentries(output_file)
                                        
                                        self.current_parent_frn = parent_frn
                                        self.current_record = {}
                                        self.sub_entries = []
                                    
                                    # Create sub-entry record with just the sub-specific data
                                    sub_record = {'frn': frn}
                                    fields = self.extract_field_value(text)
                                    sub_record.update(fields)
                                    self.sub_entries.append(sub_record)
                                else:
                                    # This is a main FRN (not a sub-entry)
                                    # Write previous parent record if exists
                                    if self.current_record and self.current_parent_frn:
                                        self._write_record_with_subentries(output_file)
                                    
                                    # Check if this is a new parent or same parent
                                    if parent_frn != self.current_parent_frn:
                                        self.current_parent_frn = parent_frn
                                        self.current_record = {'frn': parent_frn}
                                        self.sub_entries = []
                                    
                                    # Extract and merge fields for parent
                                    fields = self.extract_field_value(text)
                                    self.current_record.update(fields)
                        
                        self.pages_processed += 1
                        
                        # Progress update after each page
                        progress = (page_num / total_pages) * 100
                        print(f"Progress: {page_num:,} / {total_pages:,} pages "
                              f"({progress:.1f}%) | Records: {self.records_processed:,}")
                    
                    # Write final record
                    if self.current_record and self.current_parent_frn:
                        self._write_record_with_subentries(output_file)
            
            print("=" * 80)
            print(f"Parsing complete!")
            print(f"Pages processed: {self.pages_processed:,}")
            print(f"Records extracted: {self.records_processed:,}")
            print(f"Output file: {self.output_path}")
            print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
        except Exception as e:
            print(f"Error during parsing: {e}")
            import traceback
            traceback.print_exc()
    
    def _write_record_with_subentries(self, file_handle):
        """Write a complete parent record with its sub-entries"""
        try:
            # Add sub_entries if any exist
            if self.sub_entries:
                self.current_record['sub_entries'] = self.sub_entries
            
            # Write as JSON line
            json_line = json.dumps(self.current_record, ensure_ascii=False)
            file_handle.write(json_line + '\n')
            
            self.records_processed += 1
            
        except Exception as e:
            print(f"Error writing record {self.current_parent_frn}: {e}")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Parse FRT PDF and convert to JSON format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python parse_frt.py frt-0504.pdf frt_data.jsonl
  python parse_frt.py /path/to/input.pdf /path/to/output.jsonl
  python parse_frt.py frt-0504.pdf output.jsonl --verbose
        '''
    )
    
    parser.add_argument(
        'pdf_path',
        help='Path to the input PDF file'
    )
    parser.add_argument(
        'output_path',
        nargs='?',
        default='frt_data.jsonl',
        help='Path to the output JSONL file (default: frt_data.jsonl)'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    # Validate that PDF file exists
    if not args.pdf_path:
        parser.print_help()
        print("\nError: PDF path is required")
        return
    
    import os
    if not os.path.isfile(args.pdf_path):
        print(f"Error: PDF file not found: {args.pdf_path}")
        return
    
    parser_instance = FRTParser(args.pdf_path, args.output_path)
    parser_instance.parse()

if __name__ == '__main__':
    main()
