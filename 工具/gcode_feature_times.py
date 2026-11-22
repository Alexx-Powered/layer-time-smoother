"""Estimate layer and feature durations from a sliced Bambu/Orca Marlin file."""

import argparse
import csv
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .gcode_input import CHECKSUM, COMMAND, LAYER, MOVES, ROLE, WORD, Z_HEIGHT, arc_distance, load_gcode
else:
    from gcode_input import CHECKSUM, COMMAND, LAYER, MOVES, ROLE, WORD, Z_HEIGHT, arc_distance, load_gcode


AXES = "XYZE"
PROFILE = re.compile(r"machine_max_(speed|jerk|acceleration)_(\w+)\s*=\s*([\d.]+)")
METADATA = {"M73", "M106", "M107", "M104", "M140", "M142", "M145", "M981",
            "M991", "M1002", "M1003", "M1004", "M971", "M622.1", "G21", "G17", "G91.1"}
SECTION_NAMES = ("WIPE_TOWER", "VFLUSH", "FLUSH", "NOZZLE_CHANGE", "SKIPPABLE")
SECTION_TAGS = {name + "_" + edge: (name, edge) for name in SECTION_NAMES for edge in ("START", "END")}
SECTION_TAGS.update({name + " " + edge: (name, edge) for name in ("CP TOOLCHANGE", "CP EMPTY GRID")
                     for edge in ("START", "END")})
CHANGE_MACROS = {"M620", "M620.6", "M620.10", "M620.11", "M620.15", "M621", "M628", "M629",
                     "M983.3", "M993", "M1015.3", "M1015.4"}


@dataclass(slots=True)
class FilamentChange:
    layer: int
    start_line: int
    old: int
    new: int = -1
    end_line: int = 0
    purge_length: float = 0.0
    purge_feed: float = 0.0
    load: float = 0.0
    unload: float = 0.0
    prime: float = 0.0
    purge: float = 0.0
    sync: float = 0.0
    motion: float = 0.0
    dwell: float = 0.0


@dataclass(slots=True)
class Block:
    layer: int
    role: str
    length: float
    acceleration: float
    speed: float
    entry: float
    change: FilamentChange | None = None


def accumulate(blocks, totals):
    """Bound each junction by the braking distance ahead and the run-up behind."""
    if not blocks:
        return
    exit_speed = 0.0
    for move in reversed(blocks):
        move.entry = min(move.entry, math.sqrt(exit_speed**2 + 2 * move.acceleration * move.length))
        exit_speed = move.entry
    for i, move in enumerate(blocks):
        a, length, entry = move.acceleration, move.length, move.entry
        exit_speed = 0.0
        if i + 1 < len(blocks):
            following = blocks[i + 1]
            following.entry = min(following.entry, math.sqrt(entry**2 + 2 * a * length))
            exit_speed = following.entry
        peak = min(move.speed, math.sqrt(a * length + (entry**2 + exit_speed**2) / 2))
        cruise = max(0.0, length - (2 * peak**2 - entry**2 - exit_speed**2) / (2 * a))
        seconds = (2 * peak - entry - exit_speed) / a + cruise / peak
        totals[move.layer][move.role] += seconds
        if move.change is not None:
            move.change.motion += seconds
    blocks.clear()


def geometry(old, new, cmd, w):
    dx, dy, dz, de = (b - a for a, b in zip(old, new))
    xy = math.hypot(dx, dy)
    if cmd in {"G0", "G1"}:
        length = math.hypot(xy, dz) or abs(de)
        if length < 1e-9:
            return None
        ratio = (dx / length, dy / length, dz / length, de / length)
        return length, ratio, ratio[:3], ratio[:3], xy, None
    if w.get("P", 1) != 1:
        raise ValueError("Only single-turn XY arcs are supported.")
    cw = cmd == "G2"
    arc = arc_distance(old[:2], new[:2], w, cw)
    if arc is None or arc <= 0:
        raise ValueError("Invalid XY arc.")
    if "R" in w:
        offset = math.sqrt(max(0, w["R"]**2 - xy**2 / 4))
        sign = -1 if cw != (w["R"] < 0) else 1
        cx, cy = dx / 2 - sign * dy / xy * offset, dy / 2 + sign * dx / xy * offset
    else:
        cx, cy = w.get("I", 0), w.get("J", 0)
    radius = math.hypot(cx, cy)
    length = math.hypot(arc, dz)
    xy_ratio, z_ratio = arc / length, dz / length

    def tangent(x, y):
        r = math.hypot(x, y)
        if not r or abs(radius - r) > max(0.01, radius * 0.001):
            raise ValueError("Arc endpoint is inconsistent with its center.")
        scale = (-1 if cw else 1) * xy_ratio / r
        return -y * scale, x * scale, z_ratio

    return (length, (xy_ratio, xy_ratio, z_ratio, de / length),
            tangent(-cx, -cy), tangent(dx - cx, dy - cy), arc, radius)


def estimate(data, *, motion_feed=None, change_events=None):
    """Optionally inspect or replace the nominal mm/s of each modeled movement."""
    lines = data.decode("utf-8-sig").splitlines()
    # Use the normal-mode (first) value in slicer profile comments.
    limits = {kind: {} for kind in ("speed", "jerk", "acceleration")}
    profile = {}
    for line in lines:
        if line.startswith(";"):
            entry = line[1:].strip()
            if " = " in entry:
                name, value = entry.split(" = ", 1)
                if name in {"printer_model", "filament_nozzle_map", "machine_switch_extruder_time",
                            "machine_load_filament_time", "machine_unload_filament_time"}:
                    profile[name] = value
            if m := PROFILE.fullmatch(entry.split(",")[0]):
                limits[m[1]][m[2]] = float(m[3])
            if entry.startswith("gcode_flavor = ") and entry.split()[-1] not in {"marlin", "marlin2"}:
                raise ValueError("Direct timing currently supports Marlin-flavor G-code.")
        if line.strip() == "; EXECUTABLE_BLOCK_START":
            break
    accel_limits = limits["acceleration"]
    settings = {k: accel_limits.get(k, math.inf) for k in ("extruding", "travel", "retracting")}
    previous_direction = (0, 0, 0)
    xyz_e = [0.0] * 4
    absolute, relative_e = True, False
    feed, factor, saved_factor = 0.0, 1.0, 1.0
    role, wiping, conditional = "unclassified", False, 0
    retract_debt = 0.0
    layer = -1
    h2d = profile.get("printer_model") == "Bambu Lab H2D"
    nozzle_map = [int(v) for v in profile.get("filament_nozzle_map", "").split(",") if v]
    # This path models material resident in each nozzle, as on the supplied dual-PLA print.
    resident_dual = h2d and len(nozzle_map) == len(set(nozzle_map)) == 2
    single_ams = profile.get("printer_model") == "Bambu Lab P2S" and len(nozzle_map) > 1 and set(nozzle_map) == {0}
    if single_ams and not {"machine_load_filament_time", "machine_unload_filament_time"} <= profile.keys():
        raise ValueError("Missing single-nozzle load/unload times in the machine profile.")
    if resident_dual and not {"machine_switch_extruder_time", "machine_load_filament_time"} <= profile.keys():
        raise ValueError("Missing H2D switching or material loading time in the machine profile.")
    active_tool, loaded_tools = None, set()
    change = None
    sections = set()
    machine_flag = ""
    heights, totals, notes, blocks = [], [], [], []
    for number, line in enumerate(lines, 1):
        raw, _, comment = line.partition(";")
        raw, comment = raw.strip().upper(), comment.strip()
        if CHECKSUM.match(line):
            raise ValueError(f"Line {number}: checksummed input is unsupported.")
        if not raw:
            if single_ams and layer < 0 and line.startswith(";VT"):
                initial = re.match(r";VT(\d+)\b", line)
                if initial and int(initial[1]) < len(nozzle_map):
                    active_tool = int(initial[1])
            if single_ams and line.startswith(";VG1 ") and "CP TOOLCHANGE" in sections and not conditional:
                # Virtual E moves document firmware-internal feeding. They have their own feedrate
                # and must never change the machine's live E/F state or be emitted as real moves.
                words = dict(WORD.findall(comment[3:]))
                if set(words) != {"E", "F"} or not relative_e:
                    raise ValueError(f"Line {number}: unsupported virtual extrusion format.")
                length = abs(float(words["E"]))
                speed = min(float(words["F"]) / 60, limits["speed"]["e"])
                accel = min(settings["retracting"], accel_limits["e"])
                if speed <= 0 or accel <= 0:
                    raise ValueError(f"Line {number}: invalid virtual feed or acceleration.")
                peak = min(speed, math.sqrt(length * accel))
                seconds = length / peak + peak / accel if peak else 0.0
                flushing = "VFLUSH" in sections
                totals[layer]["virtual_flush" if flushing else "virtual_prime"] += seconds
                if change is not None:
                    if flushing:
                        change.purge += seconds
                    else:
                        change.prime += seconds
                continue
            tag = comment.split(" OF", 1)[0] if comment.startswith("NOZZLE_CHANGE_") else comment
            if tag in SECTION_TAGS:
                region, edge = SECTION_TAGS[tag]
                if (edge == "END") != (region in sections):
                    raise ValueError(f"Line {number}: unmatched {tag}.")
                if edge == "START":
                    sections.add(region)
                else:
                    sections.remove(region)
                if single_ams and change_events is not None and region == "CP TOOLCHANGE":
                    if edge == "START":
                        change = FilamentChange(layer + 1, number, active_tool)
                        change_events.append(change)
                    else:
                        change.end_line = number
                        change = None
                continue
            if LAYER.fullmatch(comment):
                layer += 1
                heights.append(None)
                totals.append(Counter())
                notes.append(set())
            elif comment in {"MACHINE_END_GCODE_START", "EXECUTABLE_BLOCK_END"}:
                break
            elif layer >= 0 and (m := Z_HEIGHT.fullmatch(comment)):
                heights[layer] = float(m[1])
            elif m := ROLE.fullmatch(comment):
                role = re.sub(r"[^a-z0-9]+", "_", m[1].casefold()).strip("_") or "unclassified"
                if role == "floating_vertical_shell":
                    role = "internal_solid_infill"
            elif comment in {"WIPE_START", "WIPE_END"}:
                wiping = comment == "WIPE_START"
            continue
        if raw.startswith("SYNC ") and (resident_dual or single_ams) and "CP TOOLCHANGE" in sections:
            if not conditional and layer >= 0:
                seconds = dict(WORD.findall(raw[4:])).get("T")
                if seconds is None:
                    raise ValueError(f"Line {number}: expected a SYNC duration.")
                accumulate(blocks, totals)
                totals[layer]["toolchange"] += float(seconds)
                if change is not None:
                    change.sync += float(seconds)
            continue
        match = COMMAND.match(raw)
        if not match:
            if layer >= 0:
                raise ValueError(f"Line {number}: unsupported command {raw.split()[0]}.")
            continue
        cmd = match[1]
        if cmd == "M1002" and raw.startswith("M1002 JUDGE_FLAG "):
            machine_flag = raw.split()[-1]
        if cmd == "M622":
            conditional += 1
            continue
        if cmd == "M623":
            conditional = max(0, conditional - 1)
            continue
        if cmd in METADATA:
            continue
        if conditional:
            # Power-loss resume repeats the parking moves only when recovering an interrupted print.
            recovery = (resident_dual or single_ams) and "CP TOOLCHANGE" in sections and machine_flag == "POWERLOSS_RESUME_FLAG"
            if layer >= 0 and not recovery:
                notes[layer].add("conditional " + cmd)
            continue
        if single_ams and cmd.startswith("T") and cmd[1:].isdigit() and int(cmd[1:]) < len(nozzle_map):
            tool = int(cmd[1:])
            if layer >= 0:
                if "CP TOOLCHANGE" not in sections or active_tool is None:
                    raise ValueError(f"Line {number}: single-nozzle change outside the expected block.")
                if change is not None:
                    change.new = tool
                if tool != active_tool:
                    accumulate(blocks, totals)
                    load, unload = float(profile["machine_load_filament_time"]), float(profile["machine_unload_filament_time"])
                    totals[layer]["toolchange"] += load + unload
                    if change is not None:
                        change.load, change.unload = load, unload
            active_tool = tool
            continue
        if resident_dual and cmd in {"T0", "T1"}:
            tool = int(cmd[1:])
            requested = dict(WORD.findall(raw[match.end():])).get("H", "-1")
            if int(float(requested)) not in {-1, nozzle_map[tool]}:
                raise ValueError(f"Line {number}: explicit nozzle differs from the resident-material mapping.")
            if layer >= 0:
                if "CP TOOLCHANGE" not in sections or active_tool is None:
                    raise ValueError(f"Line {number}: unexpected H2D tool selection.")
                accumulate(blocks, totals)
                if active_tool != tool:
                    totals[layer]["toolchange"] += float(profile["machine_switch_extruder_time"])
                if tool not in loaded_tools:
                    totals[layer]["toolchange"] += float(profile["machine_load_filament_time"])
            active_tool = tool
            loaded_tools.add(tool)
            continue
        if single_ams and cmd in {"M624", "M625"}:  # printed-object scope
            continue
        if change is not None and cmd == "M620.10":
            args = dict(WORD.findall(raw[match.end():]))
            if args.get("A") == "1":
                change.purge_length, change.purge_feed = float(args["L"]), float(args["F"])
        if (resident_dual and cmd in {"M632", "M633"}) or (
                (resident_dual or single_ams) and cmd in CHANGE_MACROS and "CP TOOLCHANGE" in sections):
            continue
        w = {k: float(v) for k, v in WORD.findall(raw[match.end():])}
        if cmd in {"M201", "M203", "M205"}:
            kind = {"M201": "acceleration", "M203": "speed", "M205": "jerk"}[cmd]
            if cmd == "M205" and "X" in w:
                limits[kind]["y"] = w["X"]
            limits[kind].update((k.lower(), v) for k, v in w.items() if k in AXES)
            if cmd == "M205" and (w.get("S", 0) or w.get("T", 0)):
                raise ValueError(f"Line {number}: nonzero minimum feedrate is unsupported.")
        elif cmd == "M204":
            updates = (("extruding", "S"), ("travel", "S"), ("retracting", "T")) if "S" in w else (
                ("extruding", "P"), ("travel", "T"), ("retracting", "R"))
            for k, letter in updates:
                if letter in w:
                    settings[k] = min(w[letter], accel_limits.get(k) or math.inf)
        elif cmd in {"G90", "G91"}:
            absolute = cmd == "G90"
        elif cmd in {"M82", "M83"}:
            relative_e = cmd == "M83"
        elif cmd == "G92":
            xyz_e = [w.get(k, v) for k, v in zip(AXES, xyz_e)] if w else [0.0] * 4
        elif cmd == "G28":
            axes = {k for k in "XYZ" if k in raw[match.end():]} or set("XYZ")
            xyz_e = [0.0 if k in axes else v for k, v in zip(AXES, xyz_e)]
            if layer >= 0:
                accumulate(blocks, totals)
                notes[layer].add("homing")
        elif cmd == "M220":
            flags = raw[match.end():].split()
            if "B" in flags:
                saved_factor = factor
            if "R" in flags:
                factor = saved_factor
            if "S" in w:
                factor = w["S"] / 100
        elif cmd in {"G4", "M400"} and layer >= 0:
            accumulate(blocks, totals)
            if w.get("U"):
                notes[layer].add("manual pause")
            dwell_layer = layer
            # Our post-processor inserts a tagged dwell during the following layer's Z hop.
            marker = re.fullmatch(r"layer_time_smoothing layer=(\d+)", comment)
            if cmd == "G4" and marker and int(marker[1]) in {layer, layer + 1} and int(marker[1]) > 0:
                dwell_layer = int(marker[1]) - 1
            delay = max(0, w.get("S", w.get("P", 0) / 1000))
            totals[dwell_layer]["dwell"] += delay
            if change is not None:
                change.dwell += delay
        elif cmd in {"G20", "G90.1", "G93", "G95", "G18", "G19", "M200"}:
            raise ValueError(f"Line {number}: unsupported motion mode {cmd}.")
        elif cmd in MOVES:
            if "F" in w:
                feed = w["F"] / 60
            old = xyz_e
            xyz_e = old.copy()
            for i, k in enumerate(AXES):
                if k in w:
                    relative = not absolute or (k == "E" and relative_e)
                    xyz_e[i] = old[i] + w[k] if relative else w[k]
            if layer < 0:
                continue
            shape = geometry(old, xyz_e, cmd, w)
            if not shape:
                continue
            length, ratios, start, end, xy, radius = shape
            extrusion = xyz_e[3] - old[3]
            pure_e = not any(start)
            bucket = "travel"
            if wiping:
                bucket = "wipe"
            elif pure_e:
                bucket = "retraction" if extrusion < 0 else "unretraction"
            elif xy and extrusion > retract_debt + 1e-8:
                bucket = role
            if sections and bucket not in {"travel", "wipe", "retraction", "unretraction"}:
                bucket = "prime_tower" if sections & {"WIPE_TOWER", "CP EMPTY GRID"} else "protected_extrusion"
            retract_debt = max(0.0, retract_debt - extrusion)
            mode = "retracting" if pure_e else "extruding" if wiping or (xy and extrusion > 0) else "travel"
            nominal = motion_feed(number - 1, layer, bucket, feed, factor, length, extrusion) if motion_feed else feed
            a, speed = settings[mode], nominal * factor
            if not math.isfinite(a) or a <= 0:
                raise ValueError(f"Line {number}: missing or invalid {mode} acceleration.")
            junction = min(speed, blocks[-1].speed) if blocks else 0.0
            if radius is not None:
                speed = min(speed, math.sqrt(a * radius) * length / xy)
            elif blocks and xy:
                prev_xy = math.hypot(*previous_direction[:2])
                cur_xy = math.hypot(*start[:2])
                if prev_xy and cur_xy:
                    turn = math.hypot(start[0] / cur_xy - previous_direction[0] / prev_xy,
                                      start[1] / cur_xy - previous_direction[1] / prev_xy)
                    if 0.00001 < turn < 0.5:
                        speed = min(speed, math.sqrt(a * xy / turn))
            for axis, component in zip("xyze", ratios):
                if component:
                    try:
                        vlim, alim = limits["speed"][axis], accel_limits[axis]
                    except KeyError:
                        raise ValueError(f"Line {number}: missing machine limits for axis {axis}.") from None
                    if vlim > 0:
                        speed = min(speed, vlim / abs(component))
                    if alim > 0:
                        a = min(a, alim / abs(component))
            if not all(math.isfinite(v) and v > 0 for v in (speed, a)):
                raise ValueError(f"Line {number}: missing or invalid speed/acceleration.")
            for axis, u, v in zip("xyz", start, previous_direction):
                if u != v and blocks:
                    if axis not in limits["jerk"]:
                        raise ValueError(f"Line {number}: missing {axis} jerk limit.")
                    junction = min(junction, limits["jerk"][axis] / abs(u - v))
            blocks.append(Block(layer, bucket, length, a, speed, min(speed, junction), change))
            previous_direction = end
        elif layer >= 0:
            notes[layer].add(cmd)
    if sections:
        raise ValueError(f"Unclosed G-code sections: {', '.join(sorted(sections))}.")
    accumulate(blocks, totals)
    if not heights or None in heights or any(b <= a for a, b in zip(heights, heights[1:])):
        raise ValueError("Expected marked planar layers with strictly increasing Z heights.")
    return heights, totals, notes


def write_change_report(path, data, events):
    if not events:
        raise ValueError("No supported single-nozzle filament change blocks found.")
    header = data.partition(b"; EXECUTABLE_BLOCK_START")[0].decode("utf-8-sig")
    profile = dict(re.findall(r"^; (filament_colour|filament_type|flush_volumes_matrix) = ([^\r\n]+)", header, re.M))
    colors = profile.get("filament_colour", "").split(";")
    types = profile.get("filament_type", "").split(";")
    matrix = [float(x) for x in profile.get("flush_volumes_matrix", "").split(",") if x]
    n = len(colors)
    if len(types) != n or len(matrix) != n * n or any(
            e.old is None or not 0 <= e.old < n or not 0 <= e.new < n or e.end_line <= e.start_line for e in events):
        raise ValueError("Incomplete change blocks or ambiguous material/volume mapping.")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("change", "layer", "start_line", "end_line", "from_tool", "from_type", "from_color",
                         "to_tool", "to_type", "to_color", "matrix_mm3", "commanded_filament_mm",
                         "commanded_feed_mm_min", "unload_s", "load_s", "virtual_prime_s", "virtual_flush_s",
                         "sync_s", "block_motion_s", "block_dwell_s", "block_estimated_s"))
        for i, e in enumerate(events, 1):
            times = (e.unload, e.load, e.prime, e.purge, e.sync, e.motion, e.dwell)
            writer.writerow((i, e.layer, e.start_line, e.end_line, f"T{e.old}", types[e.old], colors[e.old],
                             f"T{e.new}", types[e.new], colors[e.new], matrix[e.old*n + e.new], e.purge_length,
                             e.purge_feed, *(round(t, 5) for t in times), round(sum(times), 5)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--plate", type=int)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--changes", type=Path, help="write a separate single-nozzle filament-change CSV")
    args = parser.parse_args()
    try:
        if args.report.resolve() == args.input.resolve() or args.report.suffix.lower() != ".csv":
            raise ValueError("Choose a separate .csv file for the report.")
        if args.changes and (args.changes.suffix.lower() != ".csv" or
                             args.changes.resolve() in {args.report.resolve(), args.input.resolve()}):
            raise ValueError("Choose a separate .csv file for the change report.")
        data, _ = load_gcode(args.input, args.plate)
        events = [] if args.changes else None
        heights, totals, notes = estimate(data, change_events=events)
        if args.changes:
            write_change_report(args.changes, data, events)
        roles = sorted({"dwell", "travel", "retraction", "unretraction", "wipe"}.union(*(t.keys() for t in totals)))
        with args.report.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["layer", "z_mm", "estimated_s", *(r + "_s" for r in roles), "unmodeled"])
            for i, (height, row, warnings) in enumerate(zip(heights, totals, notes), 1):
                writer.writerow([i, height, round(row.total(), 5), *(round(row[r], 5) for r in roles),
                                 ", ".join(sorted(warnings))])
        print(f"{len(heights)} layers, {sum(r.total() for r in totals):.2f} estimated seconds.")
        if any(notes):
            print("See unmodeled column for commands outside the timing model.")
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
