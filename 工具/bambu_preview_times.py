"""Read per-layer estimates from Bambu Studio's Layer Time toolpath OBJ export."""

import argparse
from array import array
import csv
import hashlib
import math
from pathlib import Path

from gcode_input import LAYER, Z_HEIGHT, load_gcode


def _heights_from_data(data):
    heights = []
    for line in data.decode("utf-8-sig").splitlines():
        if not line.startswith(";"):
            continue
        comment = line[1:].strip()
        if LAYER.fullmatch(comment):
            heights.append(None)
        elif heights and heights[-1] is None and (match := Z_HEIGHT.fullmatch(comment)):
            heights[-1] = float(match[1])
    if not heights or any(height is None for height in heights):
        raise ValueError("Missing layer markers or Z heights in the reference G-code.")
    if any(b <= a for a, b in zip(heights, heights[1:])):
        raise ValueError("Expected a planar print with strictly increasing layer heights.")
    return heights


def layer_heights(path, plate=None):
    data, _ = load_gcode(path, plate)
    return _heights_from_data(data)


def read_preview(obj, heights):
    """Decode the advanced renderer's 10 vertices and 10 UVs per extrusion segment."""
    # Exporter source: AdvancedRenderer::export_toolpaths_to_obj, Bambu Studio 02.08.02.61.
    if b"map_Kd range_color.png" not in obj.with_suffix(".mtl").read_bytes().splitlines():
        raise ValueError("Expected a Bambu Studio numeric color-scheme export with its .mtl file.")
    known = {round(z, 4): i for i, z in enumerate(heights)}
    if len(known) != len(heights):
        raise ValueError("Reference layer heights are too closely spaced for this OBJ export.")
    segments = array("I")
    values = [None] * len(heights)
    vertices = uvs = 0
    with obj.open("rb") as file:
        if file.readline().strip() != b"# G-Code Toolpaths" or b"BambuStudio-" not in file.readline():
            raise ValueError("Expected a Bambu Studio toolpath OBJ.")
        for line in file:
            if line.startswith(b"v "):
                slot = vertices % 10
                if slot in (0, 4):
                    height = round(float(line.split()[3]), 4)
                    if height not in known:
                        raise ValueError(f"Extrusion at unexpected Z {height}. Check the reference G-code.")
                    if slot == 0:
                        segments.append(known[height])
                    elif known[height] != segments[-1]:
                        raise ValueError("Non-planar extrusion segment in OBJ.")
                vertices += 1
            elif line.startswith(b"vt "):
                if uvs >= vertices:
                    raise ValueError("Unexpected texture-coordinate layout.")
                if uvs % 10 == 0:
                    layer = segments[uvs // 10]
                    value = float(line.split()[1])
                    if not 0 <= value <= 1:
                        raise ValueError("Texture coordinate outside the layer-time scale.")
                    if values[layer] is not None and value != values[layer]:
                        raise ValueError("Multiple values at one Z. Select the Layer Time color scheme.")
                    values[layer] = value
                uvs += 1
            elif line.startswith(b"usemtl "):
                break
    if vertices == 0 or vertices % 10 or uvs != vertices or None in values:
        raise ValueError("Incomplete toolpaths. Export all layers with Bambu Studio's advanced renderer.")
    if min(values) != 0 or max(values) != 1:
        raise ValueError("Expected both ends of the native scale. Check the visible layers and color scheme.")
    return values


def seconds(values, minimum, maximum):
    if not (0 < minimum < maximum and math.isfinite(maximum)):
        raise ValueError("Time endpoints must be positive, finite and increasing.")
    return [minimum * math.exp(u * math.log(maximum / minimum)) for u in values]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gcode", type=Path, help="matching sliced .gcode or .gcode.3mf")
    parser.add_argument("obj", type=Path, help="OBJ exported using the Layer Time color scheme")
    parser.add_argument("--plate", type=int, help="plate number in a multi-plate archive")
    parser.add_argument("--min-seconds", type=float, required=True, help="bottom legend value")
    parser.add_argument("--max-seconds", type=float, required=True, help="top legend value")
    parser.add_argument("--output", type=Path, required=True, help="new CSV file")
    args = parser.parse_args()
    try:
        if args.output.suffix.lower() != ".csv":
            raise ValueError("Expected a new .csv output file.")
        data, _ = load_gcode(args.gcode, args.plate)
        heights = _heights_from_data(data)
        digest = hashlib.sha256(data).hexdigest()
        times = seconds(read_preview(args.obj, heights), args.min_seconds, args.max_seconds)
        with args.output.open("x", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["layer", "z_mm", "bambu_preview_s", "gcode_sha256"])
            writer.writerows((i, z, round(t, 2), digest) for i, (z, t) in enumerate(zip(heights, times), 1))
        print(f"Exported {len(times)} layer estimates.")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
