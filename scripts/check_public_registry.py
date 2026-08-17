#!/usr/bin/env python3
"""Fail when the public Command or Method ID set changes without review."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "contracts" / "golden" / "public-registry-v2.json"


def main() -> int:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    commands = json.loads(
        (ROOT / "contracts" / "commands" / "registry.json").read_text(encoding="utf-8")
    )
    actual_commands = sorted(command["id"] for command in commands["commands"])
    descriptors = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "contracts" / "methods").glob("*.method.json")
    ]
    # The golden protects the SDK-facing registry, not the internal method
    # catalog.  Counting hidden descriptors made `exposure.sdk: false` cosmetic:
    # an implementation-only method still appeared public and generators could
    # retain it forever.  Public means the descriptor explicitly opts into SDK
    # exposure; absent/false is fail-closed.
    actual_methods = sorted(
        descriptor["method_id"]
        for descriptor in descriptors
        if (descriptor.get("exposure") or {}).get("sdk") is True
    )
    failures: list[str] = []
    for label, actual in (("commands", actual_commands), ("methods", actual_methods)):
        wanted = expected[label]
        if actual != wanted:
            failures.append(
                f"{label} changed: added={sorted(set(actual) - set(wanted))}, "
                f"removed={sorted(set(wanted) - set(actual))}"
            )
    if failures:
        print("PUBLIC REGISTRY DRIFT")
        print("\n".join(failures))
        print(f"Review the API change and update {GOLDEN.relative_to(ROOT)} explicitly.")
        return 1
    print(f"public registry stable: {len(actual_commands)} Commands, {len(actual_methods)} Methods")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
