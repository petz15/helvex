import json
import sys

# Input/output file can be passed as argument, else default to noga_level_5.json
infile = sys.argv[1] if len(sys.argv) > 1 else "app/data/noga_level_5.json"
outfile = sys.argv[2] if len(sys.argv) > 2 else infile.replace(".json", "_clean.json")

with open(infile, "r", encoding="utf-8") as f:
    data = json.load(f)

# Remove unwanted annotation types
def filter_annotations(entry):
    anns = entry.get("annotations", [])
    filtered = [a for a in anns if a.get("type") not in {"EXCLUDES", "HIER_LEVEL", "INCLUDES_ALSO"}]
    entry = dict(entry)  # shallow copy
    if filtered:
        entry["annotations"] = filtered
    elif "annotations" in entry:
        del entry["annotations"]
    return entry

cleaned = [filter_annotations(e) for e in data]

with open(outfile, "w", encoding="utf-8") as f:
    json.dump(cleaned, f, ensure_ascii=False, indent=2)

print(f"Cleaned file written to {outfile}")
