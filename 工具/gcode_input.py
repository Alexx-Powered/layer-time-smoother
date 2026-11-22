"""Shared input and geometry for Bambu and Orca G-code tools."""

import math
import re
import zipfile
from pathlib import Path


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
COMMAND = re.compile(r"\s*([GMT]\d+(?:\.\d+)?)", re.IGNORECASE)
WORD = re.compile(rf"([A-Z])\s*({NUMBER})", re.IGNORECASE)
LAYER = re.compile(r"(?:CHANGE_LAYER|LAYER_CHANGE)\s*$", re.IGNORECASE)
ROLE = re.compile(r"(?:FEATURE|TYPE)\s*:\s*(.+)", re.IGNORECASE)
Z_HEIGHT = re.compile(rf"(?:Z|Z_HEIGHT)\s*:\s*({NUMBER})", re.IGNORECASE)
CHECKSUM = re.compile(r"^\s*(?:N\d+\s*[GMT]|CHECK_MD5\b|;\s*MD5\s*:)", re.IGNORECASE)
MOVES = {"G0", "G1", "G2", "G3"}
EPSILON = 1e-8


def header_total_seconds(data):
    """Read Bambu's original whole-plate estimate, including preparation."""
    header = data[:16384].decode("utf-8", errors="replace")
    match = re.search(r"total estimated time:\s*((?:\d+\s*[dhms][ \t]*)+)", header, re.I)
    if not match:
        return None
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(int(n)*units[u] for n,u in re.findall(r"(\d+)\s*([dhms])", match[1].lower())) or None


def arc_distance(start, end, words, clockwise):
    """Length of an XY arc in I/J (relative center) or R notation."""
    x, y = start
    nx, ny = end
    if "R" in words:
        radius = abs(words["R"])
        chord = math.hypot(nx - x, ny - y)
        if radius == 0 or chord > 2 * radius + 1e-4 or chord < EPSILON:
            return None
        angle = 2 * math.asin(min(1.0, chord / (2 * radius)))
        return radius * (2 * math.pi - angle if words["R"] < 0 else angle)
    if "I" not in words and "J" not in words:
        return None
    dx, dy = words.get("I", 0.0), words.get("J", 0.0)
    radius = math.hypot(dx, dy)
    if radius < EPSILON:
        return None
    if math.hypot(nx - x, ny - y) < EPSILON:
        return radius * 2 * math.pi
    start_angle = math.atan2(-dy, -dx)
    end_angle = math.atan2(ny - y - dy, nx - x - dx)
    angle = start_angle - end_angle if clockwise else end_angle - start_angle
    return radius * (angle % (2 * math.pi))


def load_gcode(path, plate):
    if not zipfile.is_zipfile(path):
        if plate is not None:
            raise ValueError("--plate only applies to a .gcode.3mf archive.")
        return path.read_bytes(), False
    with zipfile.ZipFile(path) as archive:
        entries = [entry for entry in archive.namelist() if entry.lower().endswith(".gcode")]
        if plate is not None:
            entries = [entry for entry in entries if Path(entry).name.lower() == f"plate_{plate}.gcode"]
        if len(entries) != 1:
            raise ValueError("Expected one embedded G-code file. Use --plate N for a multi-plate archive.")
        return archive.read(entries[0]), True
