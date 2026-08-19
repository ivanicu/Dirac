from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator, Mapping
from typing import Any

import jsonschema

from .provider_registry import canonical_json, sha256_digest


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_DIRECTORY = ROOT / "contracts/research/action-templates"
SCHEMA_PATH = ROOT / "contracts/domain/research/action-template.schema.json"


class ActionCatalogError(ValueError):
    pass


class ResearchActionCatalog(Mapping[str, Mapping[str, Any]]):
    def __init__(self, templates: dict[str, dict[str, Any]]) -> None:
        self._templates = templates
        self.digest = sha256_digest(
            {key: templates[key] for key in sorted(templates)}
        )

    @classmethod
    def load(
        cls, directory: pathlib.Path = DEFAULT_DIRECTORY
    ) -> "ResearchActionCatalog":
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as error:
            raise ActionCatalogError("action_template_meta_schema_invalid") from error
        try:
            registry = json.loads((directory / "registry.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ActionCatalogError("action_template_registry_unreadable") from error
        if set(registry) != {"schema_version", "templates"} or registry["schema_version"] != "1.0":
            raise ActionCatalogError("action_template_registry_shape_invalid")
        entries = registry["templates"]
        if not isinstance(entries, list) or not entries:
            raise ActionCatalogError("action_template_registry_is_empty")
        templates: dict[str, dict[str, Any]] = {}
        listed_files: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"template_id", "file"}:
                raise ActionCatalogError(f"action_template_registry_entry_{index}_invalid")
            filename = str(entry["file"])
            if pathlib.PurePath(filename).name != filename or not filename.endswith(".json"):
                raise ActionCatalogError("action_template_registry_path_is_not_local")
            if filename in listed_files:
                raise ActionCatalogError("action_template_registry_repeats_file")
            listed_files.add(filename)
            try:
                document = json.loads((directory / filename).read_text(encoding="utf-8"))
                jsonschema.Draft202012Validator(schema).validate(document)
                jsonschema.Draft202012Validator.check_schema(document["model_hint_schema"])
            except (OSError, json.JSONDecodeError, jsonschema.ValidationError,
                    jsonschema.SchemaError) as error:
                raise ActionCatalogError(
                    f"action_template_invalid:{entry['template_id']}"
                ) from error
            template_id = str(document["template_id"])
            if template_id != entry["template_id"]:
                raise ActionCatalogError("action_template_registry_id_mismatch")
            if template_id in templates:
                raise ActionCatalogError("duplicate_action_template_id")
            templates[template_id] = document
        unlisted = {
            path.name for path in directory.glob("*.json") if path.name != "registry.json"
        } - listed_files
        if unlisted:
            raise ActionCatalogError(
                "unlisted_action_template_files:" + ",".join(sorted(unlisted))
            )
        return cls(templates)

    def __getitem__(self, key: str) -> Mapping[str, Any]:
        return self._templates[key]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._templates))

    def __len__(self) -> int:
        return len(self._templates)

    def to_model_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "template_id": template_id,
                "intent": self._templates[template_id]["intent"],
                "model_hint_schema": self._templates[template_id]["model_hint_schema"],
                "risk_class": self._templates[template_id]["consequence"]["risk_class"],
                "claim_boundary": self._templates[template_id]["claim_boundary"],
            }
            for template_id in sorted(self._templates)
        ]

    def canonical_bytes(self) -> bytes:
        return canonical_json(
            {key: self._templates[key] for key in sorted(self._templates)}
        )


def default_action_catalog() -> ResearchActionCatalog:
    return ResearchActionCatalog.load()
