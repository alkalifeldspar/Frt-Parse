#!/usr/bin/env python3
"""Debug script to examine PDF structure and text extraction"""
import pdfplumber
import re

with pdfplumber.open('frt-0504.pdf') as pdf:
    print("Examining pages 3-15 to understand calibre/shots structure...")
    print("=" * 80)
    
    for page_num in range(2, min(15, len(pdf.pages))):
        page = pdf.pages[page_num]
        text = page.extract_text()
        
        if text and len(text) > 50:
            # Look for FRN and calibre/shots section
            if 'FRN' in text or 'Calibre' in text:
                print(f"\n{'='*80}")
                print(f"PAGE {page_num + 1}")
                print(f"{'='*80}")
                
                # Find FRN
                frn_match = re.search(r'Firearm Reference Number \(FRN\):\s*(\d+(?:\s*-\s*\d+)?)', text)
                if frn_match:
                    print(f"FRN: {frn_match.group(1)}")
                
                # Look for the Calibre/Shots section
                if 'Calibre' in text or 'calibre' in text:
                    # Extract from "Calibre" to the next major section
                    cal_match = re.search(r'(Calibre.*?)(?:\n(?:Legal Classification|Serial|Year|Importer|Notes)|\Z)', text, re.IGNORECASE | re.DOTALL)
                    if cal_match:
                        print("\nCalibure/Shots section:")
                        print("-" * 40)
                        section = cal_match.group(1)
                        # Show just first 500 chars for brevity
                        print(repr(section[:500]))
                        print()
