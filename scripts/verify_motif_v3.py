#!/usr/bin/env python3
"""Fail-closed verification of Motif v3 schemas and requirement traceability."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT / "backend"))
    from contracts.validation import check_schema

    schemas = sorted((ROOT / "contracts/domain/motif").glob("*.schema.json"))
    for path in schemas:
        check_schema(json.loads(path.read_text()))
    trace = json.loads((ROOT / "docs/product/motif-v3/requirements.json").read_text())
    seen = set()
    errors = []
    for requirement in trace["requirements"]:
        identifier = requirement.get("id")
        if not identifier or identifier in seen:
            errors.append(f"duplicate/missing requirement id: {identifier}")
        seen.add(identifier)
        for field in ("implementation", "tests"):
            if not requirement.get(field):
                errors.append(f"{identifier}: no {field}")
            for relative in requirement.get(field, []):
                if not (ROOT / relative).is_file():
                    errors.append(f"{identifier}: missing {relative}")
        for field in ("metric", "artifact"):
            if not requirement.get(field):
                errors.append(f"{identifier}: no {field}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Motif v3 verified: {len(schemas)} schemas, {len(seen)} traced requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
