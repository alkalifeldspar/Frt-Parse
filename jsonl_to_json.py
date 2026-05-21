#!/usr/bin/env python3
"""
Utility to convert JSONL (JSON Lines) format to standard JSON array
or to produce summary statistics about the extracted data.
"""

import json
import sys
from typing import List, Dict, Any
from pathlib import Path

def jsonl_to_json(input_path: str, output_path: str):
    """Convert JSONL file to JSON array"""
    print(f"Converting {input_path} to {output_path}")
    
    records = []
    with open(input_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping line {line_num} due to JSON error: {e}")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    
    print(f"Converted {len(records):,} records")
    print(f"Output: {output_path}")

def get_statistics(jsonl_path: str):
    """Show statistics about extracted data"""
    print(f"\nStatistics for {jsonl_path}")
    print("=" * 80)
    
    stats = {
        'total_records': 0,
        'fields_seen': {},
        'legal_classifications': {},
        'types': {},
        'actions': {},
        'countries': {},
        'makes': {},
    }
    
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    record = json.loads(line)
                    stats['total_records'] += 1
                    
                    for key, value in record.items():
                        if key not in stats['fields_seen']:
                            stats['fields_seen'][key] = 0
                        stats['fields_seen'][key] += 1
                        
                        if key == 'legal_classification':
                            stats['legal_classifications'][value] = stats['legal_classifications'].get(value, 0) + 1
                        elif key == 'type':
                            stats['types'][value] = stats['types'].get(value, 0) + 1
                        elif key == 'action':
                            stats['actions'][value] = stats['actions'].get(value, 0) + 1
                        elif key == 'country':
                            stats['countries'][value] = stats['countries'].get(value, 0) + 1
                        elif key == 'make':
                            stats['makes'][value] = stats['makes'].get(value, 0) + 1
                
                except json.JSONDecodeError:
                    pass
    
    print(f"\nTotal records: {stats['total_records']:,}")
    
    print(f"\nFields extracted (frequency):")
    for field, count in sorted(stats['fields_seen'].items(), key=lambda x: -x[1]):
        percentage = (count / stats['total_records']) * 100
        print(f"  {field}: {count:,} ({percentage:.1f}%)")
    
    print(f"\nLegal Classifications:")
    for classification, count in sorted(stats['legal_classifications'].items(), key=lambda x: -x[1]):
        print(f"  {classification}: {count:,}")
    
    print(f"\nFirearm Types:")
    for ftype, count in sorted(stats['types'].items(), key=lambda x: -x[1])[:10]:
        print(f"  {ftype}: {count:,}")
    
    print(f"\nActions:")
    for action, count in sorted(stats['actions'].items(), key=lambda x: -x[1]):
        print(f"  {action}: {count:,}")
    
    print(f"\nTop 10 Manufacturers:")
    for make, count in sorted(stats['makes'].items(), key=lambda x: -x[1])[:10]:
        print(f"  {make}: {count:,}")

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python jsonl_to_json.py <input.jsonl> [output.json]")
        print("  python jsonl_to_json.py stats <input.jsonl>")
        return
    
    command = sys.argv[1]
    
    if command == 'stats':
        if len(sys.argv) < 3:
            print("Usage: python jsonl_to_json.py stats <input.jsonl>")
            return
        get_statistics(sys.argv[2])
    else:
        input_path = command
        output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.replace('.jsonl', '.json')
        jsonl_to_json(input_path, output_path)

if __name__ == '__main__':
    main()
