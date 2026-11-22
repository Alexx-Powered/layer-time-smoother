"""Re-estimate two sliced files and write their aligned layer times as CSV."""
import argparse
import csv
from pathlib import Path

from gcode_feature_times import estimate
from gcode_input import load_gcode


def read_times(path):
    data, _ = load_gcode(path, None)
    heights, rows, _ = estimate(data)
    return heights, [row.total() for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.suffix.lower() != ".csv" or args.output.resolve() in {
            args.before.resolve(), args.after.resolve()}:
        parser.error("Choose a separate .csv output file.")
    heights, before = read_times(args.before)
    after_heights, after = read_times(args.after)
    if heights != after_heights:
        parser.error("The two slices must have matching layer counts and heights.")
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["layer", "z_mm", "before_s", "after_s"])
        for i, (height, old, new) in enumerate(zip(heights, before, after), 1):
            writer.writerow([i, height, round(old, 5), round(new, 5)])


if __name__ == "__main__":
    main()
