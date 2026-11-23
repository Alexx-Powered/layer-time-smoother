"""Slicer entry point. The host appends the path of its temporary G-code file."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
from uuid import uuid4
import zipfile

if __package__:
    from .gcode_input import load_gcode
    from .infill_speed_smoothing import MARKER, process
else:
    from gcode_input import load_gcode
    from infill_speed_smoothing import MARKER, process


def atomic_write(path, data):
    # Orca authorizes the path of this job. Reopening a numeric file descriptor
    # loses that path for its Python audit hook.
    temporary = path.with_name(".layer-smooth-" + uuid4().hex)
    stream = temporary.open("xb")
    try:
        with stream:
            stream.write(data)
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def smooth_bytes(data, strength=None, *, prepared=None):
    policy = "auto" if strength is None else f"manual-{strength:g}"
    stamp = (MARKER + "policy=" + policy + " ").encode()
    assessment = {} if strength is None else None
    output, rows = process(data,strength,mode="curve",automatic_report=assessment, prepared=prepared)
    if assessment is None:
        message = f"manual smoothness={strength:g}"
    else:
        result = assessment["exported"]
        actual = "original" if result["strength"] is None else str(result["strength"])
        message = (f"selected={actual} plate_change={result['change_pct']:+.3f}% "
                   f"jump_rms_s={result['jump_rms_s']:.4f} bend_rms_s={result['bend_rms_s']:.4f}")
    if output != data:
        marker = MARKER.encode() + b"strength="
        output = output.replace(marker,stamp + message.encode("ascii") + b" strength=",1)
    return output, rows, assessment, message


def run_job(path, strength=None, *, destination=None, audit=None, on_preview=None):
    path = Path(path)
    target = Path(destination) if destination else path
    if strength is not None and (not math.isfinite(strength) or not 0 <= strength <= 100):
        raise ValueError("Smoothness must be in [0, 100].")
    if destination and (target.suffix.lower() != ".gcode" or target.resolve() == path.resolve()):
        raise ValueError("Use a separate .gcode as the output.")
    if audit and (Path(audit).suffix.lower() != ".json" or Path(audit).resolve() in {path.resolve(),target.resolve()}):
        raise ValueError("Choose a separate .json audit file.")
    if not destination and zipfile.is_zipfile(path):
        raise ValueError("For an archive, supply --output and a separate .gcode file.")
    data, _ = load_gcode(path,None)
    policy = "auto" if strength is None else f"manual-{strength:g}"
    stamp = (MARKER + "policy=" + policy + " ").encode()
    stamps = re.findall(rb"(?m)^; infill_speed_smoothing policy=[^\r\n]+",data)
    if stamps and not destination and any(line.startswith(stamp) for line in stamps):
        if audit:
            raise ValueError("Use the original G-code to generate a new candidate audit.")
        return "Already applied: " + policy
    output, rows, assessment, message = smooth_bytes(data, strength)
    if audit:
        report = assessment if assessment is not None else {"manual_strength":strength}
        report_bytes = (json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode()
    if destination or output != data:
        atomic_write(target,output)
    if audit:
        try:
            atomic_write(Path(audit),report_bytes)
        except OSError as exc:
            raise OSError(f"G-code ready at {target}, but could not save audit {audit}: {exc}") from exc
    if on_preview and rows:
        on_preview(data, rows)
    return message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--smoothness",type=float,help="fixed strength; omit to evaluate automatically")
    parser.add_argument("--output",type=Path,help="standalone use: write a separate .gcode")
    parser.add_argument("--audit",type=Path,help="optional selection report for this job")
    args = parser.parse_args()
    try:
        print(run_job(args.input,args.smoothness,destination=args.output,audit=args.audit))
    except (OSError,ValueError,zipfile.BadZipFile) as exc:
        parser.exit(1,f"Layer smoothing: {exc}\n")


if __name__ == "__main__":
    main()
