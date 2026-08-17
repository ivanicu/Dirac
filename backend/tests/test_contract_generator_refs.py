"""Fail-closed TypeScript generation for descriptor-local schema composition."""
from __future__ import annotations

import pytest

from scripts import gen_contracts


def test_local_defs_ref_preserves_required_shape_and_tuple_type():
    schema = {
        "$defs": {
            "pair": {
                "type": "array", "minItems": 2, "maxItems": 2,
                "prefixItems": [{"type": "integer"}, {"type": "string"}],
                "items": False,
            },
            "payload": {
                "type": "object", "additionalProperties": False,
                "required": ["pair"],
                "properties": {"pair": {"$ref": "#/$defs/pair"}},
            },
        },
        "type": "object", "additionalProperties": False,
        "required": ["payload"],
        "properties": {"payload": {"$ref": "#/$defs/payload"}},
    }

    rendered = gen_contracts.ts_type(schema)

    assert "payload: {" in rendered
    assert "pair: [number, string];" in rendered
    assert "any" not in rendered


def test_one_of_is_a_union_and_all_of_is_an_intersection():
    one_of = gen_contracts.ts_type({
        "oneOf": [{"const": "left"}, {"type": "integer"}],
    })
    all_of = gen_contracts.ts_type({
        "allOf": [
            {"type": "object", "required": ["id"],
             "properties": {"id": {"type": "string"}}},
            {"type": "object", "required": ["score"],
             "properties": {"score": {"type": "number"}}},
        ],
    })

    assert one_of == '("left" | number)'
    assert " & " in all_of
    assert "id: string;" in all_of
    assert "score: number;" in all_of
    assert "unknown" not in one_of
    assert "unknown" not in all_of


@pytest.mark.parametrize(
    ("schema", "message"),
    (
        ({"$ref": "https://example.invalid/schema.json#/$defs/value"},
         "descriptor-local"),
        ({"$ref": "other.schema.json#/$defs/value"}, "descriptor-local"),
        ({"$defs": {}, "$ref": "#/$defs/missing"}, "does not resolve"),
        ({"$defs": {"bad~name": {"type": "string"}},
          "$ref": "#/$defs/bad~2name"}, "malformed JSON Pointer"),
    ),
)
def test_unknown_remote_and_malformed_refs_fail_closed(schema, message):
    with pytest.raises(SystemExit, match=message):
        gen_contracts.ts_type(schema)


def test_direct_and_indirect_ref_cycles_fail_closed():
    direct = {
        "$defs": {"loop": {"$ref": "#/$defs/loop"}},
        "$ref": "#/$defs/loop",
    }
    indirect = {
        "$defs": {
            "left": {"$ref": "#/$defs/right"},
            "right": {"$ref": "#/$defs/left"},
        },
        "$ref": "#/$defs/left",
    }

    for schema in (direct, indirect):
        with pytest.raises(SystemExit, match="circular output \\$ref"):
            gen_contracts.ts_type(schema)
