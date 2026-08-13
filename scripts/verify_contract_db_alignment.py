#!/usr/bin/env python3
"""Compare canonical ObjectKind/RelationKind vocabularies with PostgreSQL.

The JSON contracts are authoritative.  PostgreSQL is queried rather than parsed
from SQL so this check proves the schema that is actually serving requests.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _psql(dsn: str, query: str) -> list[str]:
    result = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-d", dsn, "-c", query],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    return [line for line in result.stdout.splitlines() if line]


def _difference(label: str, canonical: list[str], live: list[str]) -> str | None:
    # PostgreSQL ENUM additions are append-only while JSON is grouped for human
    # readability.  ObjectRef semantics depend on membership, never enum ordinal.
    if set(canonical) == set(live) and len(canonical) == len(live):
        return None
    return (
        f"{label} drift: missing_in_db={sorted(set(canonical) - set(live))}; "
        f"unknown_in_db={sorted(set(live) - set(canonical))}; "
        f"order_matches={canonical == live}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("DIRAC_DSN", "dbname=dirac"))
    args = parser.parse_args()
    object_contract = json.loads(
        (ROOT / "contracts" / "domain" / "object-kinds.json").read_text(encoding="utf-8")
    )["kinds"]
    relation_contract = json.loads(
        (ROOT / "contracts" / "domain" / "relations.json").read_text(encoding="utf-8")
    )["relations"]
    try:
        objects = _psql(args.dsn, "SELECT kind FROM meta.v_object_kind_registry ORDER BY ordinal")
        relations = _psql(
            args.dsn,
            "SELECT relation FROM meta.v_relation_kind_registry ORDER BY ordinal",
        )
    except RuntimeError as exc:
        print(f"contract/db alignment unavailable: {exc}")
        return 2
    problems = [
        problem
        for problem in (
            _difference("ObjectKind", object_contract, objects),
            _difference("RelationKind", relation_contract, relations),
        )
        if problem
    ]
    if problems:
        print("\n".join(problems))
        return 1
    print(
        f"contract/db alignment clean: {len(objects)} ObjectKinds, "
        f"{len(relations)} RelationKinds"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
