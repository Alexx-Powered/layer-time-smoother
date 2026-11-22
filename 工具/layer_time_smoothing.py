"""Estimate and smooth adjacent layer durations in Orca/Bambu G-code."""

import argparse
import csv
import hashlib
import math
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .gcode_feature_times import estimate
    from .gcode_input import CHECKSUM, COMMAND, EPSILON, LAYER, MOVES, ROLE, WORD, Z_HEIGHT, arc_distance, load_gcode
else:
    from gcode_feature_times import estimate
    from gcode_input import CHECKSUM, COMMAND, EPSILON, LAYER, MOVES, ROLE, WORD, Z_HEIGHT, arc_distance, load_gcode


APPLIED_MARKER = "; layer_time_smoothing layer="
MOTION_SETTINGS = {"SET_VELOCITY_LIMIT", "SET_PRESSURE_ADVANCE", "SET_FAN_SPEED", "SET_INPUT_SHAPER"}


@dataclass(slots=True)
class Layer:
    seconds: float = 0.0
    z: float | None = None
    complete: bool = True
    deposited: bool = False
    pause_before: int | None = None


def read_layers(lines):
    marlin = any(re.match(r";\s*gcode_flavor\s*=\s*marlin", line, re.I) for line in lines)
    layers = []
    layer = None
    previous_layer = None
    role = ""
    protected = set()
    conditional_depth = 0
    xyz = [None, None, None]
    extrusion_position = 0.0
    retract_debt = 0.0
    absolute_xy = True
    relative_e = False
    plane = "G17"
    feed = None
    speed_override = 1.0
    end_of_print = False
    last_z = None
    boundaries = {
        "WIPE_START": "WIPE_END",
        "WIPE_TOWER_START": "WIPE_TOWER_END",
        "FLUSH_START": "FLUSH_END",
        "VFLUSH_START": "VFLUSH_END",
        "SKIPPABLE_START": "SKIPPABLE_END",
        "MFM LAYER CHANGE START": "MFM LAYER CHANGE END",
    }

    for index, line in enumerate(lines):
        code, _, comment = line.partition(";")
        comment = comment.strip()
        if CHECKSUM.match(line):
            raise ValueError("Checksummed or line-numbered G-code is unsupported.")
        if not code.strip():
            if LAYER.fullmatch(comment):
                previous_layer = layer
                layer = Layer()
                layers.append(layer)
            elif (match := ROLE.fullmatch(comment)):
                role = match[1].casefold()
            elif (match := Z_HEIGHT.fullmatch(comment)) and layer is not None and layer.z is None:
                layer.z = float(match[1])
                if last_z is not None and layer.z <= last_z + EPSILON:
                    raise ValueError("Layer Z does not increase. Use a by-layer, planar print.")
                last_z = layer.z
            elif comment in boundaries:
                protected.add(boundaries[comment])
            elif comment in protected:
                protected.remove(comment)
            elif comment in {"MACHINE_END_GCODE_START", "EXECUTABLE_BLOCK_END"}:
                end_of_print = True
            continue

        active = layer is not None and role not in {"", "custom"} and not end_of_print
        # Count a layer from the start of deposition to the start of the next layer's deposition.
        timed_layer = previous_layer if active and previous_layer is not None and not layer.deposited else layer
        match = COMMAND.match(code)
        if not match:
            if code.strip().split()[0].upper() in MOTION_SETTINGS:
                continue
            if active:
                layer.complete = False
                timed_layer.complete = False
                xyz, feed = [None, None, None], None
                extrusion_position = None
            continue
        command = match[1].upper()
        words = {key.upper(): float(value) for key, value in WORD.findall(code[match.end():])}

        if command in {"G20", "G90.1", "G93", "G95"}:
            raise ValueError(f"Line {index + 1}: {command} is unsupported.")
        if command in {"G90", "G91"}:
            absolute_xy = command == "G90"
            if marlin:
                relative_e = not absolute_xy
        elif command in {"M82", "M83"}:
            relative_e = command == "M83"
        elif command == "M622":
            conditional_depth += 1
        elif command == "M623":
            conditional_depth = max(0, conditional_depth - 1)
        elif command in {"G17", "G18", "G19"}:
            plane = command
        elif command in {"G28", "G30"}:
            xyz = [None, None, None]
            if active:
                layer.complete = False
                timed_layer.complete = False
        elif command == "G92":
            for axis, key in enumerate("XYZ"):
                if key in words:
                    xyz[axis] = words[key]
            extrusion_position = words.get("E", extrusion_position)
        elif command == "M220" and "S" in words:
            speed_override = words["S"] / 100
        elif command == "M220" and code[match.end():].strip().upper() == "R":
            # Vendor-specific saved speed override. Wait for an explicit S value.
            speed_override = None
        elif command in {"M0", "M1", "M25", "M600", "M601", "M109", "M190"} or command.startswith("T"):
            if active:
                layer.complete = False
                timed_layer.complete = False
        elif command == "G4" and active:
            if not conditional_depth:
                timed_layer.seconds += max(0.0, words.get("S", words.get("P", 0.0) / 1000))

        if command not in MOVES:
            continue
        if "F" in words:
            feed = words["F"]
        next_xyz = xyz.copy()
        distances = []
        for axis, key in enumerate("XYZ"):
            previous = xyz[axis]
            if key not in words:
                distances.append(0.0)
            elif absolute_xy:
                next_xyz[axis] = words[key]
                distances.append(None if previous is None else words[key] - previous)
            else:
                next_xyz[axis] = None if previous is None else previous + words[key]
                distances.append(words[key])

        e_delta = 0.0
        if "E" in words:
            if relative_e or not absolute_xy:
                e_delta = words["E"]
                if extrusion_position is not None:
                    extrusion_position += e_delta
            else:
                e_delta = None if extrusion_position is None else words["E"] - extrusion_position
                extrusion_position = words["E"]
        depositing = e_delta is not None and e_delta > retract_debt + EPSILON
        if e_delta is not None:
            retract_debt = max(0.0, retract_debt - e_delta)
        old_xyz, xyz = xyz, next_xyz
        if not active:
            continue
        if None in distances or e_delta is None or speed_override is None or speed_override <= 0:
            timed_layer.complete = False
            continue
        dx, dy, dz = distances
        xy_length = math.hypot(dx, dy)
        if command in {"G2", "G3"}:
            xy_length = (arc_distance(old_xyz[:2], xyz[:2], words, command == "G2")
                         if plane == "G17" and None not in old_xyz[:2] + xyz[:2] else None)
            if xy_length is None:
                timed_layer.complete = False
                continue
        length = math.hypot(xy_length, dz) or abs(e_delta)
        if length < EPSILON:
            continue
        if not feed or feed <= 0:
            timed_layer.complete = False
            continue
        if depositing and xy_length > EPSILON and not protected and not conditional_depth:
            layer.deposited = True
            timed_layer = layer
        if conditional_depth:
            timed_layer.complete = False
            continue
        timed_layer.seconds += 60 * length / (feed * speed_override)

        # Prefer the last retracted travel at hop height before the next deposition.
        if (previous_layer is not None and previous_layer.deposited and not layer.deposited
                and layer.z is not None and previous_layer.z is not None and xyz[2] is not None
                and absolute_xy and not protected and retract_debt > 0.05 and e_delta <= 0
                and dz >= -EPSILON and math.hypot(xy_length, dz) > EPSILON
                and xyz[2] >= max(layer.z, previous_layer.z) + 0.05 - EPSILON):
            previous_layer.pause_before = index + 1

    for layer, following in zip(layers, layers[1:]):
        if not following.deposited or not following.complete:
            layer.pause_before = None
    return layers


def envelope(values, steps, upper=False):
    """Propagate a neighbor's bound in both directions in linear time."""
    result = values.copy()
    limit = min if upper else max
    sign = 1 if upper else -1
    for index, step in enumerate(steps, 1):
        result[index] = limit(result[index], result[index - 1] + sign * step)
    for index in range(len(steps) - 1, -1, -1):
        result[index] = limit(result[index], result[index + 1] + sign * steps[index])
    return result


def apply_bambu_times(path, layers, data, *, auto_selected=False):
    """Accept native preview exports and the original column of a preview comparison."""
    digest = hashlib.sha256(data).hexdigest()
    values = []
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields = reader.fieldnames or []
        column = next((name for name in ("bambu_original_s", "bambu_preview_s") if name in fields), None)
        if not column or not {"layer", "z_mm"}.issubset(fields):
            raise ValueError(f"{path.name}: expected Bambu preview timings, layer and z_mm.")
        if auto_selected and "gcode_sha256" not in fields:
            raise ValueError(f"{path.name}: older CSV has no G-code fingerprint. "
                             "Select it explicitly with --bambu-times after checking the source.")
        for number, row in enumerate(reader, 1):
            try:
                layer_id, z, seconds = int(row["layer"]), float(row["z_mm"]), float(row[column])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path.name}: invalid timing row {number}.") from exc
            if number > len(layers) or layer_id != number or layers[number - 1].z is None or not math.isclose(
                z, layers[number - 1].z, rel_tol=0, abs_tol=0.0001
            ):
                raise ValueError(f"{path.name}: layer number or Z mismatch at row {number}.")
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError(f"{path.name}: expected positive, finite seconds at row {number}.")
            if "gcode_sha256" in fields and row["gcode_sha256"] != digest:
                raise ValueError(f"{path.name}: timings belong to a different G-code.")
            values.append(seconds)
    if len(values) != len(layers):
        raise ValueError(f"{path.name}: expected {len(layers)} layers, found {len(values)}.")
    for layer, seconds in zip(layers, values):
        layer.seconds = seconds


def window_radius(strength):
    return math.ceil(96 * (strength / 100) ** 2)


def window_targets(baseline, strength):
    radius = window_radius(strength)
    weights = [(strength / 100) * (1 + math.cos(math.pi * distance / (radius + 1))) / 2
               for distance in range(radius + 1)]
    targets = baseline.copy()
    for i, current in enumerate(baseline):
        for j in range(max(0, i - radius), min(len(baseline), i + radius + 1)):
            target = current + weights[abs(i - j)] * (baseline[j] - current)
            targets[i] = max(targets[i], target)
    return targets


def plan(layers, max_step, max_pause, strength=None):
    pauses = {}
    after = [layer.seconds for layer in layers]
    usable = [i > 0 and layer.complete and layer.deposited and layer.seconds > 0
              for i, layer in enumerate(layers)]
    start = 0
    while start < len(layers):
        if not usable[start]:
            start += 1
            continue
        end = start + 1
        while end < len(layers) and usable[end]:
            end += 1
        group = layers[start:end]
        baseline = after[start:end]
        caps = [layer.seconds + (max_pause if layer.pause_before is not None else 0) for layer in group]
        jumps = [abs(a - b) for a, b in zip(baseline, baseline[1:])]
        if strength is None:
            desired = envelope(baseline, [max_step] * (len(group) - 1))
            steps = [max(max_step, jump) for jump in jumps]
        else:
            desired = window_targets(baseline, strength)
            # Taper added waits near a layer that cannot be extended (e.g. the final layer).
            steps = jumps
        feasible = envelope(caps, steps, upper=True)

        for offset, (layer, target, cap) in enumerate(zip(group, desired, feasible), start):
            pause_ms = math.floor((min(target, cap) - layer.seconds) * 1000 + EPSILON)
            if pause_ms > 0 and layer.pause_before is not None:
                pauses[layer.pause_before] = (offset + 1, pause_ms)
                after[offset] += pause_ms / 1000
        start = end
    return pauses, after, usable


def rewrite(lines, pauses):
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    for index, line in enumerate(lines):
        if index in pauses:
            layer, ms = pauses[index]
            yield f"G4 P{ms} {APPLIED_MARKER}{layer}{newline}"
        yield line


def write_gcode(destination, lines, encoding):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, newline="", delete=False,
                                         dir=destination.parent, prefix=".layer-time-") as file:
            temp_path = Path(file.name)
            file.writelines(lines)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="text G-code, or a sliced .gcode.3mf archive")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--output", type=Path, help="write a plain G-code file")
    destination.add_argument("--in-place", action="store_true", help="replace a plain G-code input (slicer hookup)")
    parser.add_argument("--plate", type=int, help="embedded plate number for a multi-plate archive")
    parser.add_argument("--report", type=Path, help="write per-layer comparison CSV")
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument("--bambu-times", type=Path, help="matching native layer-time CSV from Bambu preview")
    timing.add_argument("--direct", action="store_true", help="plan from motion estimates computed from this G-code")
    method = parser.add_mutually_exclusive_group()
    method.add_argument("--strength", type=int, choices=range(101), metavar="0..100",
                        help="neighborhood smoothing strength (0-100, default with native or direct times: 50)")
    method.add_argument("--max-step", type=float, help="target adjacent jump in seconds (legacy method)")
    parser.add_argument("--max-pause", type=float, help="maximum seconds per pause (neighborhood: 20, legacy: 2)")
    args = parser.parse_args()
    auto_selected = False
    if not args.direct and args.bambu_times is None and args.max_step is None:
        name = re.sub(r"(?:\.gcode)?\.3mf$|\.gcode$", "", args.input.name, flags=re.I)
        sidecar = args.input.with_name(name + "-Bambu-preview.csv")
        if sidecar.is_file():
            args.bambu_times = sidecar
            auto_selected = True
    if args.strength is not None and not (args.bambu_times or args.direct):
        parser.error("--strength needs --direct or matching Bambu preview timings (--bambu-times CSV).")
    if (args.bambu_times or args.direct) and args.strength is None and args.max_step is None:
        args.strength = 50
    if args.max_pause is None:
        args.max_pause = 20.0 if args.strength is not None else 2.0
    if args.max_step is None and args.strength is None:
        args.max_step = 2.0
    if not all(math.isfinite(value) and value >= 0 for value in (args.max_step, args.max_pause) if value is not None):
        parser.error("--max-step and --max-pause must be finite and nonnegative.")
    if args.max_pause * 1000 > 2**31 - 1:
        parser.error("--max-pause exceeds the G4 millisecond parameter range.")
    if args.output and (args.output.resolve() == args.input.resolve() or args.output.suffix.lower() != ".gcode"):
        parser.error("--output must be a separate .gcode file. Use --in-place to replace plain input.")
    if args.report and (args.report.resolve() == args.input.resolve() or args.report.suffix.lower() != ".csv"):
        parser.error("--report must be a separate .csv file.")
    if args.report and args.bambu_times and args.report.resolve() == args.bambu_times.resolve():
        parser.error("--report must differ from --bambu-times.")
    try:
        data, archive = load_gcode(args.input, args.plate)
        if archive and args.in_place:
            raise ValueError("Cannot replace a sliced archive. Use --output to write plain G-code.")
        encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
        lines = data.decode(encoding).splitlines(keepends=True)
        if any(APPLIED_MARKER in line for line in lines):
            if args.in_place and not args.report:
                print("Layer-time smoothing already applied. Input is unchanged.")
                return
            raise ValueError("Input was already processed. Use the original G-code to plan again.")
        layers = read_layers(lines)
        if not layers:
            raise ValueError("No Orca/Bambu layer markers found. Export sliced G-code with comments.")
        if args.bambu_times:
            apply_bambu_times(args.bambu_times, layers, data, auto_selected=auto_selected)
            print(f"Timing source: Bambu Studio preview ({args.bambu_times.name}).")
        elif args.direct:
            heights, totals, notes = estimate(data)
            for layer, height, row, warnings in zip(layers, heights, totals, notes, strict=True):
                if height != layer.z:
                    raise ValueError("Layer height mismatch between timing and pause planning.")
                layer.seconds = row.total()
                if warnings:
                    layer.complete = False
            for previous, following in zip(layers, layers[1:]):
                if not following.complete:
                    previous.pause_before = None
            print("Timing source: direct G-code motion estimate.")
        else:
            print("Timing source: path-length/feed estimate.")
        if args.strength is not None:
            print(f"Strength {args.strength}/100: up to {window_radius(args.strength)} layers on either side.")
        pauses, after, usable = plan(layers, args.max_step, args.max_pause, args.strength)
        before = [layer.seconds for layer in layers]
        pairs = [index for index in range(1, len(layers)) if usable[index - 1] and usable[index]]
        worst_before = max((abs(before[i] - before[i - 1]) for i in pairs), default=0.0)
        worst_after = max((abs(after[i] - after[i - 1]) for i in pairs), default=0.0)
        print(f"{len(layers)} layers, {len(pauses)} with added pauses.")
        print(f"Total commanded pause time: {sum(ms for _, ms in pauses.values()) / 1000:.3f} s.")
        if pairs:
            print(f"Largest comparable layer jump: {worst_before:.2f} -> {worst_after:.2f} s.")
            if args.max_step is not None:
                remaining = sum(abs(after[i] - after[i - 1]) > args.max_step + 0.001 for i in pairs)
                print(f"{remaining}/{len(pairs)} pairs above {args.max_step:g} s.")
        incomplete = sum(not layer.complete or not layer.deposited for layer in layers)
        missing_points = sum(layer.pause_before is None for layer in layers[1:-1])
        print(f"{incomplete} layers with incomplete timing. "
              f"{missing_points} intermediate layers without a retracted Z-hop pause point.")
        if args.report:
            with args.report.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["layer", "z_mm", "estimated_before_s", "pause_ms", "estimated_after_s", "status"])
                by_layer = {layer: ms for layer, ms in pauses.values()}
                for index, (layer, old, new) in enumerate(zip(layers, before, after), 1):
                    status = "paused" if index in by_layer else "unchanged"
                    if not layer.complete or not layer.deposited:
                        status = "incomplete"
                    elif index == 1:
                        status = "first_layer"
                    elif layer.pause_before is None:
                        status = "no_pause_point"
                    writer.writerow([index, layer.z, round(old, 6), by_layer.get(index, 0), round(new, 6), status])
        if args.output:
            write_gcode(args.output, rewrite(lines, pauses), encoding)
        elif args.in_place and pauses:
            write_gcode(args.input, rewrite(lines, pauses), encoding)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
