"""Build the offline explorer from the public example timings and feasible limits."""
import base64
import csv
import json
import math
from pathlib import Path
import struct

from infill_speed_smoothing import DEFAULT_SMOOTHNESS
from layer_uniformity import curved_targets


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CASES = (
    ("stone", "讲解石", "P2S · 单色"),
    ("h2d", "H2D", "H2D · 双喷头"),
    ("multicolor", "肥肥嘟", "P2S · 三色与异料支撑"),
)


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def case_data(slug, name, description):
    layers = read_rows(EXAMPLES / f"{slug}-layers.csv")
    limits = read_rows(EXAMPLES / f"{slug}-limits.csv")
    audit = json.loads((EXAMPLES / f"{slug}-audit.json").read_text(encoding="utf-8"))
    numbers = [int(row["layer"]) for row in layers]
    if numbers != list(range(1, len(layers) + 1)) or numbers != [int(row["layer"]) for row in limits]:
        raise ValueError(f"Misaligned layers in {slug}")
    fastest = [float(row["fastest_s"]) for row in limits]
    editable = [bool(int(row["editable"])) for row in limits]
    before = [float(row["before_s"]) for row in layers]
    after = [float(row["after_s"]) for row in layers]
    block = struct.Struct(f"<{len(layers)}f")
    profiles = bytearray()
    for tick in range(201):
        strength = tick / 2
        values = curved_targets(fastest, editable, strength)
        if strength == audit["exported"]["strength"] and max(abs(x-y) for x, y in zip(values, after)) > 0.05:
            raise ValueError(f"Preview differs from exported G-code in {slug}")
        profiles.extend(block.pack(*values))
    return dict(name=name, description=description, file=f"{slug}-layers.csv",
                total=audit["original"]["total_s"], original=before, originalSum=math.fsum(before),
                z=[float(row["z_mm"]) for row in layers], profiles=base64.b64encode(profiles).decode("ascii"))


if __name__ == "__main__":
    data = dict(steps=2, default=DEFAULT_SMOOTHNESS, cases=[case_data(*case) for case in CASES])
    html = Path(__file__).with_name("layer_explorer.html").read_text(encoding="utf-8")
    token = "__EMBEDDED_LAYER_DATA__"
    if html.count(token) != 1:
        raise ValueError("Missing data placeholder in template")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    (EXAMPLES / "layer-time-explorer.html").write_text(html.replace(token, payload), encoding="utf-8")
