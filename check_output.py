#!/usr/bin/env python3
import json

# Check the output records
with open('output.jsonl', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if i >= 5:
            break
        record = json.loads(line)
        print(f"Record {i+1} (FRN {record.get('frn')}):")
        print(f"  - calibre: {record.get('calibre')}")
        print(f"  - shots: {record.get('shots')}")
        print(f"  - barrel_length: {record.get('barrel_length')}")
        print(f"  - sub_entries: {'Yes' if record.get('sub_entries') else 'No'}")
        print()
