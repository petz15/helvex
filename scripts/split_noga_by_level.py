import json

# Load the flat NOGA lookup table
with open("noga_lookup.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Group entries by level_no
by_level = {}
for code, entry in data.items():
    level = entry.get("level_no")
    if level is not None:
        by_level.setdefault(level, []).append({**entry, "code": code})

# Save each level to a separate file
for level, entries in by_level.items():
    out_path = f"noga_level_{level}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(entries)} entries to {out_path}")
