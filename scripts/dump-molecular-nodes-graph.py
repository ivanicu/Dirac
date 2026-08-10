"""Extract the Molecular Nodes Geometry/Shader graph closure from a .blend file.

Run with Blender, for example:

    blender --background --python scripts/dump-molecular-nodes-graph.py -- \
        node_data_file.blend molecular-nodes.graph.json

The output intentionally excludes Blender UI state and scene meshes. It is the
portable compiler input for the browser representation runtime.
"""

from __future__ import annotations

import json
import hashlib
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

import bpy


ROOT_GROUPS = (
    "Style Ball and Stick",
    "Style Cartoon",
    "Style Preset 1",
    "Style Preset 2",
    "Style Preset 3",
    "Style Preset 4",
    "Style Ribbon",
    "Style Spheres",
    "Style Sticks",
    "Style Surface",
    "Color AO",
    "Flat",
    "Outline Mask",
    "Transparent Outline",
)

IGNORED_NODE_PROPERTIES = {
    "rna_type",
    "name",
    "label",
    "location",
    "width",
    "width_hidden",
    "height",
    "dimensions",
    "select",
    "show_options",
    "show_preview",
    "show_texture",
    "show_expanded",
    "hide",
    "parent",
    "inputs",
    "outputs",
    "internal_links",
}


def cli_paths() -> tuple[Path, Path]:
    try:
        separator = sys.argv.index("--")
        blend, output = sys.argv[separator + 1 : separator + 3]
    except (ValueError, IndexError):
        raise SystemExit("expected: -- <source.blend> <output.json>")
    return Path(blend).resolve(), Path(output).resolve()


def json_value(raw: Any) -> Any:
    if raw is None or isinstance(raw, (str, int, bool)):
        return raw
    if isinstance(raw, float):
        return raw if math.isfinite(raw) else str(raw)
    if hasattr(raw, "name") and hasattr(raw, "bl_rna"):
        return {"id": raw.name, "type": raw.bl_rna.identifier}
    if isinstance(raw, (list, tuple)) or hasattr(raw, "__iter__"):
        try:
            return [json_value(value) for value in raw]
        except (TypeError, ValueError, RuntimeError):
            return None
    return None


def socket_data(socket: Any) -> dict[str, Any]:
    default = None
    if hasattr(socket, "default_value"):
        try:
            default = json_value(socket.default_value)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    return {
        "name": socket.name,
        "identifier": socket.identifier,
        "type": socket.bl_idname,
        "default": default,
        "linked": bool(socket.is_linked),
    }


def node_properties(node: Any) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for prop in node.bl_rna.properties:
        name = prop.identifier
        if name in IGNORED_NODE_PROPERTIES or prop.is_hidden:
            continue
        try:
            value = json_value(getattr(node, name))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue
        if value is not None:
            properties[name] = value
    return properties


def group_data(group: Any) -> dict[str, Any]:
    dependencies: set[str] = set()
    nodes = []
    for node in group.nodes:
        nested = getattr(node, "node_tree", None)
        if nested is not None:
            dependencies.add(nested.name)
        nodes.append(
            {
                "name": node.name,
                "label": node.label,
                "type": node.bl_idname,
                "node_tree": nested.name if nested is not None else None,
                "properties": node_properties(node),
                "inputs": [socket_data(socket) for socket in node.inputs],
                "outputs": [socket_data(socket) for socket in node.outputs],
            }
        )
    links = [
        {
                "from_node": link.from_node.name,
                "from_socket": link.from_socket.name,
                "from_socket_id": link.from_socket.identifier,
                "to_node": link.to_node.name,
                "to_socket": link.to_socket.name,
                "to_socket_id": link.to_socket.identifier,
        }
        for link in group.links
    ]
    return {
        "name": group.name,
        "type": group.bl_idname,
        "dependencies": sorted(dependencies),
        "nodes": sorted(nodes, key=lambda item: item["name"]),
        "links": sorted(
            links,
            key=lambda item: (
                item["to_node"],
                item["to_socket_id"],
                item["to_socket"],
                item["from_node"],
                item["from_socket_id"],
                item["from_socket"],
            ),
        ),
    }


def main() -> None:
    blend_path, output_path = cli_paths()
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))

    materials = {
        material.name: group_data(material.node_tree)
        for material in bpy.data.materials
        if material.node_tree is not None
    }

    material_dependencies = {
        dependency
        for material in materials.values()
        for dependency in material["dependencies"]
    }
    selected: dict[str, dict[str, Any]] = {}
    queue = deque(sorted(set(ROOT_GROUPS) | material_dependencies))
    while queue:
        name = queue.popleft()
        if name in selected:
            continue
        group = bpy.data.node_groups.get(name)
        if group is None:
            continue
        data = group_data(group)
        selected[name] = data
        queue.extend(
            dependency
            for dependency in data["dependencies"]
            if dependency not in selected
        )

    result = {
        "schema": "molecular-nodes.blend-graph@1",
        "source": {
            "blend": blend_path.name,
            "blender": bpy.app.version_string,
            "blendHash": hashlib.sha256(blend_path.read_bytes()).hexdigest(),
        },
        "roots": list(ROOT_GROUPS),
        "all_node_groups": sorted(group.name for group in bpy.data.node_groups),
        "selected_groups": dict(sorted(selected.items())),
        "materials": dict(sorted(materials.items())),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "all_groups": len(result["all_node_groups"]),
                "selected_groups": len(selected),
                "materials": len(materials),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
