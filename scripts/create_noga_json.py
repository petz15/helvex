import json
import pathlib
import argparse
import re

from typing import Any

'''
use json file from:
https://www.i14y.admin.ch/de/catalog/concepts/08dd28d2-a693-5049-a3fe-0ee83005b61b/content. 
'''

DIR = pathlib.Path("C:\\D\\coding_projects\\zefix_analyzer\\")
INPUT_FILE = "CodelistEntries_nogaCode-2.0.0.json"
OUTPUT_TREE_FILE = "app/data/noga_tree.json"
OUTPUT_LOOKUP_FILE = "app/data/noga_lookup.json"


_CODE_PATTERNS: list[tuple[str, re.Pattern[str], int, str]] = [
    ("abschnitt", re.compile(r"^[A-Z]$"), 1, "Abschnitt"),
    ("abteilung", re.compile(r"^\d{2}$"), 2, "Abteilung"),
    ("gruppe", re.compile(r"^\d{3}$"), 3, "Gruppe"),
    ("klasse", re.compile(r"^\d{4}$"), 4, "Klasse"),
    ("art", re.compile(r"^\d{6}$"), 5, "Art"),
]


# ---------------------------
# IO
# ---------------------------
def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_items(payload: Any) -> list[dict[str, Any]]:
    """Normalize supported input payloads to a list of codelist entries.

    Supported source shapes:
    - list[dict]: already flat list of nodes
    - {"data": list[dict]}: BFS codelist export format
    """
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        items = payload["data"]
    else:
        raise ValueError("Unsupported input JSON shape. Expected list or object with 'data' list.")

    normalized: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not code:
            raise ValueError(f"Entry at index {i} is missing required field 'code'.")
        normalized.append(item)
    return normalized


def classify_code(code: str) -> tuple[str, int, str]:
    """Return (level_key, level_no, level_label_de) for a NOGA code."""
    for level_key, pattern, level_no, level_label_de in _CODE_PATTERNS:
        if pattern.match(code):
            return level_key, level_no, level_label_de
    raise ValueError(
        f"Unsupported code '{code}'. Expected: Abschnitt(A-Z), Abteilung(2), Gruppe(3), Klasse(4), Art(6)."
    )


def expected_parent(code: str, level_key: str, source_parent: str | None) -> str | None:
    """Derive/validate parent code from official hierarchy rules."""
    if level_key == "abschnitt":
        return None
    if level_key == "abteilung":
        # Only this relation cannot be derived from the numeric code itself.
        return source_parent
    if level_key == "gruppe":
        return code[:2]
    if level_key == "klasse":
        return code[:3]
    if level_key == "art":
        return code[:4]
    return None


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------
# Normalization
# ---------------------------
def normalize_annotations(annotations):
    """
    Keep only relevant fields and drop empty entries.
    """
    normalized = []

    for a in annotations or []:
        if not isinstance(a, dict):
            continue
        entry = {
            "type": a.get("type")
        }

        text = a.get("text")
        if text:
            entry["text"] = text

        # skip empty annotations (no text + no meaningful type)
        if entry.get("type") or entry.get("text"):
            normalized.append(entry)

    return normalized


# ---------------------------
# Level detection
# ---------------------------
def extract_level(node):
    """
    Extract hierarchy level from HIER_LEVEL annotation.
    """
    for ann in node.get("annotations", []):
        if not isinstance(ann, dict):
            continue
        if ann.get("type") == "HIER_LEVEL":
            text = ann.get("text", {})
            if isinstance(text, dict):
                return text.get("en") or text.get("de") or text.get("fr") or text.get("it")
            if isinstance(text, str):
                return text
    return None


# ---------------------------
# Build index
# ---------------------------
def build_index(items):
    index = {}

    for item in items:
        if "code" not in item:
            continue
        node = dict(item)  # shallow copy
        code = str(item["code"])
        level_key, level_no, level_label_de = classify_code(code)

        # normalize annotations
        node["annotations"] = normalize_annotations(
            node.get("annotations")
        )

        # authoritative level derived from code pattern (spec-compliant)
        node["level"] = level_label_de
        node["level_key"] = level_key
        node["level_no"] = level_no

        # keep optional source annotation level only for debugging/comparison
        source_level = extract_level(node)
        if source_level:
            node["source_level"] = source_level

        # prepare children container
        node["children"] = []

        if code in index:
            raise ValueError(f"Duplicate code encountered: {code}")
        index[code] = node

    return index


# ---------------------------
# Build tree
# ---------------------------
def build_tree(items):
    index = build_index(items)
    roots = []
    issues: list[str] = []

    for item in items:
        if "code" not in item:
            continue
        code = str(item["code"])
        parent_code = item.get("parentCode")
        parent_code = str(parent_code) if parent_code is not None else None

        node = index[code]
        level_key = str(node.get("level_key"))
        effective_parent = expected_parent(code, level_key, parent_code)

        if effective_parent and effective_parent in index:
            parent_node = index[effective_parent]
            parent_key = str(parent_node.get("level_key"))

            # Enforce legal hierarchy edges.
            valid_edge = (
                (level_key == "abteilung" and parent_key == "abschnitt")
                or (level_key == "gruppe" and parent_key == "abteilung")
                or (level_key == "klasse" and parent_key == "gruppe")
                or (level_key == "art" and parent_key == "klasse")
            )
            if not valid_edge:
                issues.append(
                    f"Invalid edge: {code}({level_key}) -> {effective_parent}({parent_key})"
                )
            parent_node["children"].append(node)
        else:
            roots.append(node)

        if level_key == "abteilung":
            if not effective_parent or not re.match(r"^[A-Z]$", effective_parent):
                issues.append(f"Abteilung {code} requires section parent (A-Z), got: {effective_parent!r}")
        elif level_key in {"gruppe", "klasse", "art"}:
            # For these levels we derive deterministic parent from code.
            if parent_code and parent_code != effective_parent:
                issues.append(
                    f"Parent mismatch for {code}: source={parent_code}, expected={effective_parent}"
                )

    if issues:
        preview = "\n".join(issues[:25])
        raise ValueError(
            f"Hierarchy validation failed with {len(issues)} issue(s). First issues:\n{preview}"
        )

    return roots, index


# ---------------------------
# Sorting
# ---------------------------
def sort_tree(nodes):
    nodes.sort(key=lambda x: str(x.get("code", "")))

    for node in nodes:
        if node["children"]:
            sort_tree(node["children"])


# ---------------------------
# Cleanup
# ---------------------------
def clean_tree(nodes):
    """
    Remove empty children arrays recursively.
    """
    for node in nodes:
        if node.get("children"):
            clean_tree(node["children"])
        else:
            node.pop("children", None)


# ---------------------------
# Lookup map
# ---------------------------
def build_lookup(index):
    """
    Flat lookup map: code -> node (without children to keep it lightweight)
    """
    lookup = {}

    for code, node in index.items():
        flat_node = dict(node)
        flat_node.pop("children", None)
        lookup[code] = flat_node

    return lookup


# ---------------------------
# MAIN
# ---------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build hierarchical and lookup NOGA JSON files from a flat BFS export."
    )
    parser.add_argument("--base-dir", type=pathlib.Path, default=DIR)
    parser.add_argument("--input", default=INPUT_FILE, help="Input JSON filename or absolute path")
    parser.add_argument("--out-tree", default=OUTPUT_TREE_FILE, help="Output tree JSON filename")
    parser.add_argument("--out-lookup", default=OUTPUT_LOOKUP_FILE, help="Output lookup JSON filename")
    return parser.parse_args()


def _resolve_path(base_dir: pathlib.Path, value: str) -> pathlib.Path:
    p = pathlib.Path(value)
    return p if p.is_absolute() else (base_dir / p)


def main():
    args = parse_args()
    base_dir = args.base_dir.resolve()

    full_file_src = _resolve_path(base_dir, args.input)
    out_tree = _resolve_path(base_dir, args.out_tree)
    out_lookup = _resolve_path(base_dir, args.out_lookup)

    payload = load_data(full_file_src)
    items = extract_items(payload)

    tree, index = build_tree(items)

    sort_tree(tree)
    clean_tree(tree)

    lookup = build_lookup(index)

    save_json(out_tree, tree)
    save_json(out_lookup, lookup)

    print(f"Loaded {len(items)} items from: {full_file_src}")
    print(f"Root nodes: {len(tree)}")
    print(f"Wrote tree: {out_tree}")
    print(f"Wrote lookup: {out_lookup}")


if __name__ == "__main__":
    main()