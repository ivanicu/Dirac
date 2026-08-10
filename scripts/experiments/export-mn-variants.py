"""Build representative Molecular Nodes assets and export comparable web artifacts.

Run through Blender:
    blender --background --python scripts/experiments/export-mn-variants.py -- \
        <molecular-nodes-repo> <input.cif> <output-directory>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def arguments() -> tuple[Path, Path, Path]:
    separator = sys.argv.index("--")
    repository, structure, output = sys.argv[separator + 1 : separator + 4]
    return Path(repository).resolve(), Path(structure).resolve(), Path(output).resolve()


repository, structure_path, output_root = arguments()
sys.path[:0] = [
    str(repository / ".venv/lib/python3.13/site-packages"),
    str(repository),
]

import bpy  # noqa: E402
import molecularnodes as mn  # noqa: E402


VARIANTS = (
    {
        "id": "spheres-point",
        "asset": "spheres",
        "parameters": {"sphere_geometry": "Point", "quality": 2, "scale": 0.8},
        "web_strategy": "point-cloud",
    },
    {
        "id": "spheres-instance-q1",
        "asset": "spheres",
        "parameters": {"sphere_geometry": "Instance", "quality": 1, "scale": 0.8},
        "web_strategy": "instance-source",
    },
    {
        "id": "spheres-instance-q3",
        "asset": "spheres",
        "parameters": {"sphere_geometry": "Instance", "quality": 3, "scale": 0.8},
        "web_strategy": "instance-source-high",
    },
    {
        "id": "spheres-mesh-q2",
        "asset": "spheres",
        "parameters": {"sphere_geometry": "Mesh", "quality": 2, "scale": 0.8},
        "web_strategy": "realized-mesh",
    },
    {
        "id": "cartoon-q1",
        "asset": "cartoon",
        "parameters": {"quality": 1},
        "web_strategy": "evaluated-mesh-low",
    },
    {
        "id": "cartoon-q2",
        "asset": "cartoon",
        "parameters": {"quality": 2},
        "web_strategy": "evaluated-mesh-medium",
    },
    {
        "id": "cartoon-q3",
        "asset": "cartoon",
        "parameters": {"quality": 3},
        "web_strategy": "evaluated-mesh-high",
    },
    {
        "id": "ribbon-q2",
        "asset": "ribbon",
        "parameters": {"quality": 2},
        "web_strategy": "evaluated-ribbon-mesh",
    },
)


def evaluated_summary(obj: bpy.types.Object) -> dict[str, object]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
    try:
        return {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "triangles": sum(max(0, len(polygon.vertices) - 2) for polygon in mesh.polygons),
            "polygons": len(mesh.polygons),
            "attributes": sorted(attribute.name for attribute in mesh.attributes),
            "materials": [material.name for material in mesh.materials if material],
        }
    finally:
        bpy.data.meshes.remove(mesh)


def export_variant(variant: dict[str, object]) -> dict[str, object]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mn.session.get_session().clear()

    start = time.perf_counter()
    molecule = mn.Molecule.load(structure_path)
    molecule.add_style(variant["asset"], **variant["parameters"])
    bpy.context.view_layer.update()
    build_seconds = time.perf_counter() - start

    obj = molecule.object
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    geometry = evaluated_summary(obj)

    blend_path = output_root / f"{variant['id']}.blend"
    glb_path = output_root / f"{variant['id']}.glb"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), copy=True, relative_remap=False)

    export_start = time.perf_counter()
    result = bpy.ops.export_scene.gltf(
        filepath=str(glb_path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_attributes=True,
    )
    export_seconds = time.perf_counter() - export_start
    if result != {"FINISHED"}:
        raise RuntimeError(f"glTF export failed for {variant['id']}: {result}")

    return {
        **variant,
        "source": structure_path.name,
        "buildSeconds": round(build_seconds, 6),
        "exportSeconds": round(export_seconds, 6),
        "blendBytes": blend_path.stat().st_size,
        "glbBytes": glb_path.stat().st_size,
        "geometry": geometry,
    }


def main() -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        mn.ui.addon._test_register()
    except Exception:
        # Registration is process-global and may already have happened in an interactive run.
        pass
    results = []
    for variant in VARIANTS:
        print(f"MN_EXPERIMENT_START {variant['id']}", flush=True)
        result = export_variant(variant)
        results.append(result)
        print(f"MN_EXPERIMENT_DONE {json.dumps(result, sort_keys=True)}", flush=True)
    manifest = {
        "schema": "molecular-compiler-lab@1",
        "blender": bpy.app.version_string,
        "molecularNodes": "5.2.0",
        "source": str(structure_path),
        "variants": results,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"MN_EXPERIMENT_MANIFEST {output_root / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
