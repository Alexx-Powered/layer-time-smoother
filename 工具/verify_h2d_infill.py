"""Check Bambu/Orca source and adjusted moves independently of the time planner."""

import argparse
import csv
import hashlib
import re
import zipfile
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path


NUMBER = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
FEED = re.compile(rb"F\s*(" + NUMBER + rb")", re.I)
WORDS = re.compile(rb"([XYE])\s*(" + NUMBER + rb")", re.I)
MOTION = re.compile(rb"^(G[0123])(?=[\sFXYZEIJR]|$)", re.I)
INFILL = {b"Sparse infill", b"Internal solid infill", b"Floating vertical shell"}
PAIRS = {
    b"WIPE_TOWER_START": (b"WIPE_TOWER_END", "tower"),
    b"CP TOOLCHANGE START": (b"CP TOOLCHANGE END", "toolchange"),
    b"CP EMPTY GRID START": (b"CP EMPTY GRID END", "empty_grid"),
    b"NOZZLE_CHANGE_START": (b"NOZZLE_CHANGE_END", "nozzle"),
    b"VFLUSH_START": (b"VFLUSH_END", "flush"),
    b"FLUSH_START": (b"FLUSH_END", "material_flush"),
    b"WIPE_START": (b"WIPE_END", "wipe"),
    b"SKIPPABLE_START": (b"SKIPPABLE_END", "skippable"),
}
STARTS = {key: group for key, (_, group) in PAIRS.items()}
ENDS = {end: group for end, group in PAIRS.values()}


def open_plate(stack, path, plate):
    if zipfile.is_zipfile(path):
        bundle = stack.enter_context(zipfile.ZipFile(path))
        return stack.enter_context(bundle.open(f"Metadata/plate_{plate}.gcode"))
    return stack.enter_context(path.open("rb"))


class Trace:
    def __init__(self, protected_tools=()):
        self.digest = hashlib.sha256()
        self.support_digest = hashlib.sha256()
        self.support_lines = 0
        self.protected_tools = set(protected_tools)
        self.material_digest = hashlib.sha256()
        self.material_lines = 0
        self.protected_lines = 0
        self.starts = Counter()
        self.tool_commands = Counter()
        self.dwells = Counter()
        self.annotations = 0
        self.layers = 0

    def records(self, lines):
        sections = set()
        conditionals = 0
        body_finished = False
        role = b""
        feed = None
        active_tool = None
        x = y = e = 0.0
        xyz_absolute, e_relative = True, False

        for line_no, line in enumerate(lines, 1):
            raw, _, comment = line.partition(b";")
            code, tag = raw.strip(), comment.strip()
            op = code.split(None, 1)[0].upper() if code else b""
            boundary = tag.split(None, 1)[0] if tag.startswith(b"NOZZLE_CHANGE_") else tag
            if not code:
                if tag in {b"CHANGE_LAYER", b"LAYER_CHANGE"}:
                    self.layers += 1
                elif tag in {b"MACHINE_END_GCODE_START", b"EXECUTABLE_BLOCK_END"}:
                    body_finished = True
                elif tag.startswith((b"FEATURE:", b"TYPE:")):
                    role = tag.partition(b":")[2].strip()
                if boundary in STARTS:
                    group = STARTS[boundary]
                    if group in sections:
                        raise ValueError(f"Line {line_no}: unexpected nested {group} section.")
                    sections.add(group)
                    self.starts[group] += 1
            if op == b"M622":
                conditionals += 1
            if not conditionals and op.startswith(b"T") and op[1:].isdigit() and int(op[1:]) < 255:
                active_tool = op.decode()
            protected = (self.layers <= 1 or body_finished or bool(sections) or
                         role == b"Prime tower" or conditionals > 0)
            if protected:
                self.digest.update(line)
                self.protected_lines += 1
            if self.layers and not body_finished and role in {b"Support", b"Support transition", b"Support interface"}:
                self.support_digest.update(line)
                self.support_lines += 1
            if self.layers and not body_finished and active_tool in self.protected_tools:
                self.material_digest.update(line)
                self.material_lines += 1
                protected = True
            if not code and boundary in ENDS:
                group = ENDS[boundary]
                if group not in sections:
                    raise ValueError(f"Line {line_no}: unmatched end of {group} section.")
                sections.remove(group)
            if op == b"M623":
                conditionals -= 1
                if conditionals < 0:
                    raise ValueError(f"Line {line_no}: unmatched M623.")
            if not code and tag.startswith(b"infill_speed_smoothing "):
                self.annotations += 1
                continue
            if op in (b"G90", b"G91"):
                xyz_absolute = op == b"G90"
            if op in (b"M82", b"M83"):
                e_relative = op == b"M83"
            if op.startswith(b"T") and op[1:].isdigit() and int(op[1:]) < 255 and self.layers and not body_finished:
                self.tool_commands[op.decode()] += 1
            if op == b"G4" and self.layers and not body_finished:
                self.dwells[code.decode()] += 1

            allowed = False
            motion = MOTION.match(code)
            if motion or op == b"G92":
                coords = {k.upper(): float(v) for k, v in WORDS.findall(code)}
                if op == b"G92":
                    x, y, e = coords.get(b"X", x), coords.get(b"Y", y), coords.get(b"E", e)
                else:
                    f = FEED.search(code)
                    if f:
                        feed = Decimal(f[1].decode("ascii"))
                    nx = coords.get(b"X", x) if xyz_absolute else x + coords.get(b"X", 0)
                    ny = coords.get(b"Y", y) if xyz_absolute else y + coords.get(b"Y", 0)
                    ne = e + coords.get(b"E", 0) if e_relative or not xyz_absolute else coords.get(b"E", e)
                    arc = motion[1].upper() in (b"G2", b"G3") and any(k in code.upper() for k in (b"I", b"J", b"R"))
                    xy_path = nx != x or ny != y or arc
                    allowed = not protected and role in INFILL and ne > e and xy_path
                    x, y, e = nx, ny, ne
                    if f:
                        # Standalone modal F settings can be inserted to restore an inherited feed.
                        # Everything in the protected regions is also compared byte-for-byte.
                        code = code[:f.start()] + code[f.end():]
                        if op == b"G1" and code.strip().upper() == b"G1" and not tag:
                            continue

            canonical = (b" ".join(code.split()), tag)
            yield line_no, canonical, feed, allowed, self.layers, role

        if sections or conditionals:
            raise ValueError("Unclosed protection section at end of file.")


def verify(original, processed, plate, protected_tools=(), *, single_material=False, auto_report=None):
    source, result = Trace(protected_tools), Trace(protected_tools)
    counts, changed_layers = Counter(), set()
    smallest = largest = None
    policies = {}
    ordinary_counts = Counter()
    ordinary_example = None
    if auto_report:
        with auto_report.open(encoding="utf-8-sig", newline="") as stream:
            for i, row in enumerate(csv.DictReader(stream), 1):
                policy = row["feed_policy"]
                if (int(row["layer"]) != i or policy not in {"ordinary_150", "smooth", "protected"}
                        or policy == "ordinary_150" and Decimal(row["infill_feed_pct"]) != 150):
                    raise ValueError(f"Invalid reported policy on layer {i}.")
                policies[i] = policy
    with ExitStack() as stack:
        left = source.records(open_plate(stack, original, plate))
        right = result.records(open_plate(stack, processed, plate))
        for before, after in zip_longest(left, right):
            if not before or not after or before[1] != after[1]:
                raise ValueError(f"Changed command, comment or path near input/output lines "
                                 f"{before and before[0]}/{after and after[0]}.")
            if before[3] and policies.get(before[4]) == "ordinary_150":
                error = before[2] * Decimal("1.5") - after[2]
                if not Decimal(0) <= error <= Decimal("0.0001"):
                    raise ValueError(f"Ordinary infill below 150% at input line {before[0]}.")
                ordinary_counts[before[4]] += 1
                if ordinary_example is None:
                    ordinary_example = before, after
            if before[2] == after[2]:
                continue
            if policies.get(before[4]) == "protected":
                raise ValueError(f"Feed changed on protected layer {before[4]}.")
            if not before[3] or not after[3] or not before[2] or not after[2]:
                raise ValueError(f"Unexpected modal feed change at input/output lines {before[0]}/{after[0]}.")
            ratio = after[2] / before[2]
            if not 0 < ratio <= Decimal("1.5"):
                raise ValueError(f"Out-of-range feed ratio {ratio} at input line {before[0]}.")
            smallest = ratio if smallest is None else min(ratio, smallest)
            largest = ratio if largest is None else max(ratio, largest)
            counts[before[5].decode()] += 1
            changed_layers.add(before[4])
    if auto_report and len(policies) != source.layers:
        raise ValueError("Report layer count differs from source.")
    if auto_report and set(ordinary_counts) != {i for i, policy in policies.items() if policy == "ordinary_150"}:
        raise ValueError("A reported ordinary layer contains no independently recognized infill.")
    if not single_material and (not source.starts["tower"] or not source.starts["toolchange"] or not source.tool_commands):
        raise ValueError("Expected a plate containing marked tower and tool-change sections.")
    if (source.digest.digest() != result.digest.digest() or source.protected_lines != result.protected_lines or
            source.starts != result.starts or source.layers != result.layers or source.annotations or result.annotations > 1):
        raise ValueError("Protected bytes, section counts or smoothing markers differ unexpectedly.")
    if source.support_digest.digest() != result.support_digest.digest() or source.support_lines != result.support_lines:
        raise ValueError("Support, support transition or support interface bytes changed.")
    if source.protected_tools and (not source.material_lines or source.material_lines != result.material_lines or
                                   source.material_digest.digest() != result.material_digest.digest()):
        raise ValueError("Requested protected material missing or its command stream changed.")
    print(f"Layers {source.layers}, changed layers {len(changed_layers)}, changed motions {dict(counts)}.")
    if auto_report:
        print(f"Independently checked 150% on {ordinary_counts.total()} eligible moves in "
              f"{len(ordinary_counts)} ordinary layers.")
    if ordinary_example:
        before, after = ordinary_example
        print(f"Layer {before[4]}, {before[5].decode()}, input line {before[0]} F{before[2]} "
              f"-> output line {after[0]} F{after[2]} mm/min.")
    if counts:
        print(f"Effective feed range {100 * smallest:.6f}% to {100 * largest:.6f}%.")
    print(f"Protected raw lines {source.protected_lines}, SHA-256 {source.digest.hexdigest()}.")
    print(f"Support-labeled raw lines {source.support_lines}, SHA-256 {source.support_digest.hexdigest()}.")
    if source.protected_tools:
        print(f"Protected material lines {source.material_lines}, SHA-256 {source.material_digest.hexdigest()}.")
    print(f"Tagged sections {dict(source.starts)}, in-print tool commands {dict(source.tool_commands)}, "
          f"in-print dwells {dict(source.dwells)}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("processed", type=Path)
    parser.add_argument("--plate", type=int, default=1)
    parser.add_argument("--single-material", action="store_true", help="allow a plate with no tower or changes")
    parser.add_argument("--auto-report", type=Path, help="also verify the ordinary 150%% and protected layer policies")
    parser.add_argument("--protect-tool", action="append", default=[], help="also check the material's raw bytes, e.g. T5")
    args = parser.parse_args()
    try:
        verify(args.original, args.processed, args.plate, args.protect_tool,
               single_material=args.single_material, auto_report=args.auto_report)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Verification failed: {exc}\n")
