"""Smooth layer time by changing the feed of sparse and internal solid infill."""

import argparse
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .automatic_curve import rate_curve, select_curve
    from .gcode_feature_times import estimate
    from .gcode_input import NUMBER, load_gcode
    from .layer_uniformity import curved_targets, plateau_targets, relative_jump, uniform_targets
    from .layer_time_smoothing import APPLIED_MARKER, Layer, apply_bambu_times, window_radius, write_gcode
else:
    from automatic_curve import rate_curve, select_curve
    from gcode_feature_times import estimate
    from gcode_input import NUMBER, load_gcode
    from layer_uniformity import curved_targets, plateau_targets, relative_jump, uniform_targets
    from layer_time_smoothing import APPLIED_MARKER, Layer, apply_bambu_times, window_radius, write_gcode


INFILL = {"sparse_infill", "internal_solid_infill"}
CAP = 1.5
MAX_TIME_RATIO = 1.5
DEFAULT_JUMP_SECONDS = 3.0
DEFAULT_JUMP_PERCENT = 20.0
TIME_TOLERANCE = 0.1  # seconds per whole plate, after serialization
MARKER = "; infill_speed_smoothing "
FEED_WORD = re.compile(rf"(F\s*)({NUMBER})", re.I)
AMS_COLUMNS = ("change_count", "load_unload_s", "virtual_prime_s", "virtual_flush_s", "flush_sync_s",
               "full_change_blocks_s")
UNIFORM_COLUMNS = ("jump_before_pct", "jump_after_pct", "requested_jump_pct", "feasible_jump_pct", "edge_status")
PLATEAU_COLUMNS = ("plateau_id", "plateau_target_s", "plateau_spread_s")
PLATEAU_TOLERANCE = 0.01  # full max-min range in seconds, after writing the file
DEFAULT_SMOOTHNESS = 65.0
CURVE_TOLERANCE = 0.05  # maximum per-layer difference from the planned curve, in seconds


@dataclass(slots=True)
class InfillMove:
    layer: int
    feed: float  # mm/min, before M220


def targets(baseline, fastest, editable, strength):
    """Fit a smooth curve above each layer's reachable minimum time."""
    radius = window_radius(strength)
    if not radius or len(baseline) < 2:
        return baseline.copy()
    # The first layer runs its own adhesion profile. Other fixed layers anchor their neighbors.
    preferred = baseline.copy()
    for i in range(1, len(baseline)):
        neighbors = range(max(1, i - radius), min(len(baseline), i + radius + 1))
        weights = [1 + math.cos(math.pi * (j - i) / (radius + 1)) for j in neighbors]
        mean = sum(baseline[j] * w for j, w in zip(neighbors, weights)) / sum(weights)
        preferred[i] += (mean - baseline[i]) * strength / 100

    curve = [max(lo, t) if enabled else b for lo, t, enabled, b in zip(fastest, preferred, editable, baseline)]
    # Minimize squared deviation from the local mean plus the squared adjacent jumps.
    # Clamped layers participate in the neighbor updates on both sides.
    penalty = (radius / 4) ** 2
    relaxation = 2 / (1 + math.sqrt(1 - (2 * penalty / (1 + 2 * penalty)) ** 2))
    for _ in range(10000):
        movement = 0.0
        for i in range(1, len(curve)):
            if not editable[i]:
                continue
            neighbors = []
            if i > 1:
                neighbors.append(curve[i - 1])
            if i + 1 < len(curve):
                neighbors.append(curve[i + 1])
            best = (preferred[i] + penalty * sum(neighbors)) / (1 + penalty * len(neighbors))
            fitted = max(fastest[i], curve[i] + relaxation * (best - curve[i]))
            movement = max(movement, abs(fitted - curve[i]))
            curve[i] = fitted
        if movement < 0.00001:
            break
    else:
        raise ValueError("Bounded layer-time fit failed to converge.")
    return curve


def budget_curve(fastest, editable, pressure):
    """Least squared adjacent jumps plus a per-second cost, with a floor at 150% time.

    Layer 1 is disconnected. A free interval has constant second difference
    (pressure), so solve each interval exactly when its neighboring floor lifts.
    """
    t = fastest.copy()
    free = [False] * len(t)
    last = len(t) - 1
    while True:
        lifted = False
        for i in range(1, len(t)):
            if not editable[i] or free[i]:
                continue
            pull = (t[i-1] - t[i] if i > 1 else 0) + (t[i+1] - t[i] if i < last else 0)
            if pull > pressure + 1e-9:
                free[i] = lifted = True
        if not lifted:
            return t
        i = 1
        while i <= last:
            if not free[i]:
                i += 1
                continue
            left = i - 1
            while i <= last and free[i]:
                i += 1
            right = i
            for j in range(left + 1, right):
                if left == 0:
                    t[j] = t[right] - pressure * (right-j) * (right+j-1) / 2
                elif right > last:
                    t[j] = t[left] - pressure * (j-left) * (2*last+1-j-left) / 2
                else:
                    t[j] = (t[left] + (t[right]-t[left]) * (j-left) / (right-left)
                            - pressure * (j-left) * (right-j) / 2)


def global_targets(baseline, fastest, editable, requested=None):
    # At this pressure even the most pronounced short layer stays at its fastest feed.
    threshold = max((max(0.0, (fastest[i-1]-v if i > 1 else 0) +
                         (fastest[i+1]-v if i+1 < len(fastest) else 0))
                     for i, v in enumerate(fastest) if i > 0 and editable[i]), default=0.0)

    def at(strength):
        return budget_curve(fastest, editable, threshold * (1-strength/100)**2)

    if requested is not None:
        return requested, at(requested)
    total = math.fsum(baseline)
    if abs(math.fsum(fastest) - total) < 0.0001:
        return 0.0, fastest.copy()
    if math.fsum(fastest) > total + TIME_TOLERANCE:
        raise ValueError("The all-150% estimate already exceeds the original time.")
    if math.fsum(at(100)) < total - TIME_TOLERANCE:
        raise ValueError("Insufficient short-layer compensation to match the original time while keeping ordinary infill at 150%.")
    lo, hi = 0.0, 100.0
    for _ in range(48):
        strength = (lo + hi) / 2
        curve = at(strength)
        if math.fsum(curve) < total:
            lo = strength
        else:
            hi = strength
    return strength, curve


def speed_first_targets(baseline, fastest, editable, jump_seconds, jump_percent):
    """Find the fastest times satisfying neighbor tolerances, relaxing them only at the plate cap."""
    if any(not math.isfinite(v) or v < 0 for v in (jump_seconds, jump_percent)):
        raise ValueError("Neighbor jump tolerances must be finite and nonnegative.")
    ratio = 1 + jump_percent / 100
    limit = MAX_TIME_RATIO * math.fsum(baseline)
    if math.fsum(fastest) > limit:
        raise ValueError("Even the fastest eligible infill exceeds the plate time limit.")

    def envelope(slack):
        # For a taller neighbor h, require h <= max(t + seconds, t * ratio) + slack.
        # Both bounds are monotone; one pass from either side propagates every peak.
        t = fastest.copy()
        def raise_short(i, h):
            if editable[i]:
                needed = min(h - slack - jump_seconds, (h - slack) / ratio)
                t[i] = max(t[i], needed)
        for i in range(2, len(t)):
            raise_short(i, t[i-1])
        for i in range(len(t)-2, 0, -1):
            raise_short(i, t[i+1])
        return t

    # Include only edges where lifting the short layer is possible. Fixed layers
    # still act as anchors, including at the ends. The first layer is disconnected.
    max_slack = 0.0
    for i in range(2, len(fastest)):
        low, high = sorted((i-1, i), key=fastest.__getitem__)
        if editable[low]:
            excess = fastest[high] - max(fastest[low]+jump_seconds, ratio*fastest[low])
            max_slack = max(max_slack, excess)
    if not max_slack:
        return 0.0, fastest.copy()
    goal = envelope(0)
    if math.fsum(goal) <= limit:
        return 100.0, goal
    # Keep the smallest relaxation that fits. At max_slack the all-fast curve fits.
    lo, hi = 0.0, max_slack
    best = fastest.copy()
    for _ in range(48):
        slack = (lo + hi) / 2
        trial = envelope(slack)
        if math.fsum(trial) <= limit:
            hi, best = slack, trial
        else:
            lo = slack
    return 100 * (1 - hi / max_slack), best


def choose_scales(baseline, fastest, target, editable, simulate, global_mode=False):
    """Invert the actual acceleration-limited time response for each layer together."""
    low = [1.0] * len(target)
    high = low.copy()
    for i, goal in enumerate(target):
        if not editable[i]:
            continue
        if global_mode and goal <= fastest[i] + 1e-9:
            low[i] = high[i] = CAP
            continue
        if abs(goal - baseline[i]) < (1e-8 if global_mode else 0.002):
            continue
        if goal < baseline[i]:
            high[i] = CAP
            if goal <= fastest[i] + (1e-9 if global_mode else 0.002):
                low[i] = CAP
        else:
            low[i] = 0.5
    # Find a slow enough endpoint only on layers that need additional time.
    while any(a < 1 for a in low):
        measured = simulate(low)
        margin = 0.00001 if global_mode else 0.002
        short = [i for i, (a, t) in enumerate(zip(low, measured)) if a < 1 and t < target[i] - margin]
        if not short:
            break
        for i in short:
            low[i] /= 2
            if low[i] < 1e-7:
                raise ValueError(f"Layer {i+1}: no positive representable feed reaches its target.")
    for _ in range(23 if global_mode else 16):
        scales = [math.sqrt(a*b) if global_mode and a != b else (a+b)/2 for a, b in zip(low, high)]
        if all(a == b for a, b in zip(low, high)):
            break
        measured = simulate(scales)
        for i, t in enumerate(measured):
            if low[i] == high[i]:
                continue
            if abs(t - target[i]) < (0.00001 if global_mode else 0.002):
                low[i] = high[i] = scales[i]
            elif t > target[i]:
                low[i] = scales[i]
            else:
                high[i] = scales[i]
    return [(a + b) / 2 for a, b in zip(low, high)]


def feed_number(value):
    return f"{value:.10f}".rstrip("0").rstrip(".")


def render(data, moves, scales, strength):
    encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    lines = data.decode(encoding).splitlines(keepends=True)
    output = []
    restored_feed = None
    emitted_feed = None
    marked = False
    for index, line in enumerate(lines):
        move = moves.get(index)
        scale = scales[move.layer] if move else 1.0
        ending = "\r\n" if line.endswith("\r\n") else "\n"
        if scale == 1.0:
            # Restore the modal F before any other original line, including opaque printer macros.
            if restored_feed is not None:
                output.append(f"G1 F{feed_number(restored_feed)}{ending}")
                restored_feed = emitted_feed = None
            output.append(line)
            continue
        if not marked:
            output.append(f"{MARKER}strength={strength} max_feed_percent=150{ending}")
            marked = True
        code, sep, comment = line.rstrip("\r\n").partition(";")
        token = FEED_WORD.search(code)
        # Round down so serialization cannot push a requested 150% past the hard cap.
        desired = math.floor(move.feed * scale * 10000) / 10000
        if desired <= 0:
            raise ValueError(f"Line {index + 1}: requested feed rounds to zero.")
        if token:
            code = code[:token.start(2)] + feed_number(desired) + code[token.end(2):]
        elif desired != emitted_feed:
            code += " F" + feed_number(desired)
        output.append(code + sep + comment + line[len(line.rstrip("\r\n")):])
        emitted_feed = desired
        restored_feed = move.feed
    if restored_feed is not None:
        output.append(f"\nG1 F{feed_number(restored_feed)}\n")
    return "".join(output).encode(encoding)


def mean_jump(values):
    jumps = [abs(b - a) for a, b in zip(values[1:], values[2:])]
    return sum(jumps) / len(jumps) if jumps else 0.0


def reconcile_total(scales, compensating, evaluate, result):
    """Remove residual plate error due to junction coupling or F rounding on written moves."""
    # evaluate returns (serialized bytes, direct rows, corrected times, total time error).
    if abs(result[-1]) <= TIME_TOLERANCE:
        return scales, result
    if not any(compensating):
        raise ValueError("No compensation layer is available to correct the serialized plate time.")
    def probe(multiplier):
        trial = [min(CAP, rate * multiplier) if use else rate for rate, use in zip(scales, compensating)]
        return trial, evaluate(trial)

    # A common correction keeps ordinary infill pinned, and preserves the relative
    # allocation on compensation layers until one reaches the cap.
    start = 1.0
    stop = 1.0
    increase_feed = result[-1] > 0
    for _ in range(24):
        stop = stop * 2 if increase_feed else stop / 2
        trial, end = probe(stop)
        if abs(end[-1]) <= TIME_TOLERANCE:
            return trial, end
        if (end[-1] > 0) != increase_feed:
            break
        if increase_feed and all(not use or rate == CAP for rate, use in zip(trial, compensating)):
            raise ValueError("All adjustable feeds reached 150% before the original time was recovered.")
        start = stop
    else:
        raise ValueError("Could not bracket the original time with positive compensation feeds.")
    lo, hi = sorted((start, stop))
    for _ in range(32):
        mid = math.sqrt(lo * hi)
        trial, end = probe(mid)
        if abs(end[-1]) <= TIME_TOLERANCE:
            return trial, end
        if end[-1] > 0:
            lo = mid
        else:
            hi = mid
    raise ValueError("Feed precision prevents matching the original time within the requested tolerance.")


def enforce_time_cap(scales, editable, evaluate, result, allowed_extra):
    """Keep a verified, under-cap output even when F rounding or junctions shift elapsed time."""
    if result[-1] <= allowed_extra:
        return scales, result
    upper = [CAP if e else r for e, r in zip(editable, scales)]
    best = upper, evaluate(upper)
    if best[1][-1] > allowed_extra:
        raise ValueError("The written all-fast G-code exceeds 150% of the original modeled time.")
    lo, hi = 0.0, 1.0
    for _ in range(32):
        if allowed_extra - best[1][-1] <= TIME_TOLERANCE:
            break
        mix = (lo + hi) / 2
        rates = [r + mix*(u-r) for r, u in zip(scales, upper)]
        trial = evaluate(rates)
        if trial[-1] <= allowed_extra:
            hi, best = mix, (rates, trial)
        else:
            lo = mix
    return best


def prepare_input(data, reference=None, *, motion_cache=None):
    """Parse one immutable source. Interactive clients can reuse its motion limits."""
    if MARKER.encode() in data or APPLIED_MARKER.encode() in data:
        raise ValueError("Use the original sliced file as input.")
    moves = {}

    def inspect(index, layer, role, feed, override, length, extrusion):
        if layer > 0 and role in INFILL:
            moves[index] = InfillMove(layer, feed * 60)
        return feed

    events = []
    heights, rows, notes = estimate(data, motion_feed=inspect, change_events=events,
                                   record_motion=motion_cache.record if motion_cache else None)
    direct = [r.total() for r in rows]
    baseline = direct.copy()
    if reference:
        layers = [Layer(z=h) for h in heights]
        apply_bambu_times(reference, layers, data)
        baseline = [layer.seconds for layer in layers]
    eligible_layers = {m.layer for m in moves.values()}
    editable = [i in eligible_layers and not n for i, n in enumerate(notes)]

    def simulate(scales):
        if motion_cache is not None:
            return motion_cache.simulate(scales)
        def adjusted(index, layer, role, feed, override, length, extrusion):
            return feed * scales[layer] if index in moves else feed

        _, simulated, _ = estimate(data, motion_feed=adjusted)
        return [b + r.total() - d for b, r, d in zip(baseline, simulated, direct)]

    if motion_cache is not None:
        motion_cache.finish(baseline)
    fastest = simulate([CAP if e else 1.0 for e in editable])
    return moves, events, heights, rows, notes, direct, baseline, editable, simulate, fastest


def process(data, strength, reference=None, *, mode="legacy",
            jump_seconds=DEFAULT_JUMP_SECONDS, jump_percent=DEFAULT_JUMP_PERCENT,
            preferred_jump=5.0, maximum_jump=8.0, plateau_band=12.0, automatic_report=None,
            prepared=None):
    state = prepared if prepared is not None else prepare_input(data, reference)
    moves, events, heights, rows, notes, direct, baseline, editable, simulate, fastest = state
    uniform_mode = mode in {"uniform", "plateau"}
    global_mode = uniform_mode or mode in {"auto", "equal", "global", "curve"}
    def plan_uniform(floors):
        curve, limits, bounds = uniform_targets(floors, editable, preferred_jump, maximum_jump, original=baseline)
        groups = []
        if mode == "plateau":
            curve, groups = plateau_targets(curve, editable, bounds, plateau_band)
        return curve, limits, bounds, groups

    if automatic_report is not None:
        if mode != "curve":
            raise ValueError("Automatic selection requires continuous curve smoothing.")
        target, decision = select_curve(data, baseline, fastest, editable)
        automatic_report.update(decision)
        strength = decision["planned"]["strength"]
        if strength is None:
            automatic_report["exported"] = decision["planned"]
            return data, []
    elif mode == "curve":
        target = curved_targets(fastest, editable, strength)
    elif uniform_mode:
        target, requested, log_bounds, groups = plan_uniform(fastest)
    elif mode == "auto":
        strength, target = speed_first_targets(baseline, fastest, editable, jump_seconds, jump_percent)
    elif global_mode:
        strength, target = global_targets(baseline, fastest, editable, None if mode == "equal" else strength)
    else:
        target = targets(baseline, fastest, editable, strength)
    scales = choose_scales(baseline, fastest, target, editable, simulate, global_mode)
    annotation = f"{strength:.8f} mode={mode}" if global_mode else strength
    if uniform_mode:
        annotation = f"adaptive mode={mode} preferred_jump_pct={preferred_jump:g} max_jump_pct={maximum_jump:g}"
        if mode == "plateau":
            annotation += f" plateau_band_pct={plateau_band:g}"
    if mode == "auto":
        annotation += f" max_time_percent=150 jump_seconds={jump_seconds:g} jump_percent={jump_percent:g}"
    def evaluate(rates):
        output = render(data, moves, rates, annotation)
        _, final, final_notes = estimate(output)
        if notes != final_notes:
            raise ValueError("Unexpected change to unmodeled instructions after rewriting.")
        after = [b + r.total() - d for b, r, d in zip(baseline, final, direct)]
        return output, final, after, math.fsum(after) - math.fsum(baseline)

    result = evaluate(scales)
    if mode == "auto":
        allowed_extra = MAX_TIME_RATIO * math.fsum(baseline) - math.fsum(baseline)
        scales, result = enforce_time_cap(scales, editable, evaluate, result, allowed_extra)
    elif mode == "equal":
        compensating = [e and t > f + 1e-9 for e, t, f in zip(editable, target, fastest)]
        scales, result = reconcile_total(scales, compensating, evaluate, result)
    output, final, after, delta = result
    if mode == "curve":
        # The motion planner can couple neighboring layers at their junction.
        # Use the measured lower bounds if this changes the exported curve.
        for attempt in range(3):
            error = max((abs(a-b) for a,b in zip(after,target)), default=0)
            if error <= CURVE_TOLERANCE:
                break
            if attempt == 2:
                raise ValueError(f"Exported curve misses its target by {error:.4f} s.")
            floors = [t if e and scale < CAP else actual
                      for t,e,scale,actual in zip(fastest,editable,scales,after)]
            target = curved_targets(floors, editable, strength)
            scales = choose_scales(baseline, floors, target, editable, simulate, True)
            output, final, after, delta = evaluate(scales)
        print(f"Smoothness {strength:g}, maximum serialized layer error {error:.5f} s.")
        if automatic_report is not None:
            actual = rate_curve(strength,after,automatic_report["original"])
            automatic_report["evaluated_export"] = actual
            original = automatic_report["candidates"][0]
            if not actual["eligible"] or actual["score"] >= original["score"]-1e-9:
                automatic_report["exported"] = original
                return data, []
            automatic_report["exported"] = actual
    elif uniform_mode:
        # Slower neighboring motions can change the entry/exit speed of a layer
        # running at the cap. Re-measure such anchors before certifying the bound.
        local_floor = fastest.copy()
        for attempt in range(4):
            bounds = [100*math.expm1(v) for v in log_bounds]
            missed = [i for i in range(2, len(after)) if min(fastest[i-1:i+1]) > 0
                      and relative_jump(after, i) > bounds[i] + 0.02]
            spreads = [max(after[a:b+1])-min(after[a:b+1]) for a,b in groups]
            if not missed and max(spreads, default=0) <= PLATEAU_TOLERANCE:
                break
            if attempt == 3:
                if not missed:
                    raise ValueError(f"Serialized plateau spread {max(spreads):.5f} s exceeds {PLATEAU_TOLERANCE} s.")
                i = missed[0]
                raise ValueError(f"Layer {i+1}: serialized jump {relative_jump(after,i):.3f}% "
                                 f"exceeds its feasible bound {bounds[i]:.3f}%.")
            for i, rate in enumerate(scales):
                if rate == CAP or not editable[i]:
                    local_floor[i] = after[i]
            target, requested, log_bounds, groups = plan_uniform(local_floor)
            scales = choose_scales(baseline, local_floor, target, editable, simulate, True)
            output, final, after, delta = evaluate(scales)
        print(f"Original {sum(baseline):.2f} s, {mode} {sum(after):.2f} s; "
              f"{sum(b > r + 1e-6 for b,r in zip(bounds[2:],requested[2:]))} fixed-boundary exceptions.")
        plateau_info = {}
        for number, ((a,b), spread) in enumerate(zip(groups, spreads), 1):
            for i in range(a,b+1):
                plateau_info[i] = (number, round(target[a],5), round(spread,7))
        if mode == "plateau":
            print(f"{len(groups)} plateaus, {len(plateau_info)} layers, worst max-min {max(spreads, default=0):.7f} s.")
    elif global_mode:
        print(f"Global strength {strength:.6f}, serialized plate delta {delta:+.5f} s.")
    if mode == "auto":
        original_time, output_time = math.fsum(baseline), math.fsum(after)
        percent = f" ({100 * output_time / original_time:.3f}%)" if original_time > 0 else ""
        print(f"Original {original_time:.3f} s, output {output_time:.3f} s{percent}, "
              f"limit {MAX_TIME_RATIO * original_time:.3f} s.")
        full_speed_time = math.fsum(fastest)
        ordinary = [i for i, available in enumerate(editable) if available and target[i] == fastest[i]]
        saved_on_ordinary = math.fsum(baseline[i] - after[i] for i in ordinary)
        print(f"All-fast saving {original_time - full_speed_time:.3f} s; "
              f"smoothing added {output_time - full_speed_time:.3f} s over all-fast. "
              f"{len(ordinary)} ordinary layers saved {saved_on_ordinary:.3f} s versus their originals.")
    changes_by_layer = [[] for _ in heights]
    for e in events:
        changes_by_layer[e.layer - 1].append(e)
    records = []
    for i, h in enumerate(heights):
        state = ("first_layer" if i == 0 else "unmodeled" if notes[i] else "no_infill"
                 if not editable[i] else "at_150pct" if scales[i] == CAP else "adjustable")
        records.append((i+1, h, round(baseline[i], 5), round(fastest[i], 5), round(target[i], 5),
                        round(scales[i] * 100, 6), round(after[i], 5), round(final[i].total(), 5), state,
                        round(rows[i]["toolchange"], 5), round(rows[i]["prime_tower"], 5)))
        if events:
            changes = changes_by_layer[i]
            load = sum(e.load + e.unload for e in changes)
            prime = sum(e.prime for e in changes)
            flush = sum(e.purge for e in changes)
            sync = sum(e.sync for e in changes)
            full = load + prime + flush + sync + sum(e.motion + e.dwell for e in changes)
            records[-1] += (len(changes), *(round(t, 5) for t in (load, prime, flush, sync, full)))
        if global_mode:
            policy = ("protected" if not editable[i] else "ordinary_150" if scales[i] == CAP
                      else "smooth")
            if uniform_mode:
                records[-1] += (policy,)
                if i < 2:
                    records[-1] += ("", "", "", "", "excluded")
                elif min(fastest[i-1:i+1]) == 0 or min(baseline[i-1:i+1]) == 0:
                    records[-1] += ("", "", "", "", "no_duration")
                else:
                    reason = ("fixed_ramp" if bounds[i] > requested[i]+1e-6 else
                              "local_cost" if requested[i] > preferred_jump+1e-6 else "preferred")
                    records[-1] += (round(relative_jump(baseline,i),5), round(relative_jump(after,i),5),
                                    round(requested[i],5), round(bounds[i],5), reason)
                if mode == "plateau":
                    records[-1] += plateau_info.get(i, ("", "", ""))
            else:
                records[-1] += (round(strength, 8), policy)
    print(f"{len(heights)} layers; {sum(f != 1 for f in scales)} adjusted; "
          f"{sum(f == CAP for f in scales)} at 150%.")
    print(f"Mean adjacent jump from layer 2: {mean_jump(baseline):.3f} -> {mean_jump(after):.3f} s.")
    return output, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--plate", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoothness", type=float, metavar="0..100",
                      help=f"continuous layer-time curve, from fastest to smoothest (default {DEFAULT_SMOOTHNESS:g})")
    mode.add_argument("--plateau", dest="policy", action="store_const", const="plateau",
                      help="previous short-oscillation plateau method")
    mode.add_argument("--uniform", dest="policy", action="store_const", const="uniform",
                      help="previous relative layer-time limits without plateaus")
    parser.add_argument("--plateau-band", type=float, help="maximum peak-to-trough range of a small oscillation (default 12%%)")
    mode.add_argument("--strength", type=int, choices=range(101), metavar="0..100",
                      help="original fixed-strength smoothing method")
    mode.add_argument("--global-strength", type=float, metavar="0..100",
                      help="smooth from the all-150%% profile using this global index")
    mode.add_argument("--auto-time", dest="policy", action="store_const", const="auto",
                      help="previous speed-first mode with 150%% plate time cap")
    mode.add_argument("--equal-time", dest="policy", action="store_const", const="equal",
                      help="previous equal-time smoothing policy")
    parser.add_argument("--jump-seconds", type=float, help="absolute neighbor tolerance in auto mode (default 3 s)")
    parser.add_argument("--jump-percent", type=float, help="relative tolerance vs. shorter layer in auto mode (default 20%%)")
    parser.add_argument("--preferred-jump", type=float, help="preferred relative jump in uniform mode (default 5%%)")
    parser.add_argument("--maximum-jump", type=float, help="maximum adaptive jump in uniform mode (default 8%%)")
    parser.add_argument("--bambu-times", type=Path, help="use matching original preview times as baseline")
    args = parser.parse_args()
    try:
        if args.output.suffix.lower() != ".gcode" or args.output.resolve() == args.input.resolve():
            raise ValueError("Choose a separate .gcode output.")
        if args.report and (args.report.suffix.lower() != ".csv" or
                            args.report.resolve() in {p.resolve() for p in (args.input, args.bambu_times) if p}):
            raise ValueError("Choose a separate .csv report.")
        old_tolerances = args.jump_seconds is not None or args.jump_percent is not None
        old_bounds = any(v is not None for v in (args.preferred_jump, args.maximum_jump, args.plateau_band))
        mode = args.policy or ("auto" if old_tolerances else "plateau" if old_bounds else "curve")
        if args.smoothness is not None:
            mode = "curve"
        elif args.strength is not None:
            mode = "legacy"
        elif args.global_strength is not None:
            mode = "global"
        if mode != "auto" and old_tolerances:
            raise ValueError("Neighbor tolerances apply only to --auto-time.")
        if mode not in {"uniform", "plateau"} and (args.preferred_jump is not None or args.maximum_jump is not None):
            raise ValueError("Relative jump targets apply only to --uniform and --plateau.")
        if mode != "plateau" and args.plateau_band is not None:
            raise ValueError("--plateau-band applies only to --plateau.")
        strength = args.global_strength if mode == "global" else args.strength if mode == "legacy" else 100
        if mode == "curve":
            strength = DEFAULT_SMOOTHNESS if args.smoothness is None else args.smoothness
        if not 0 <= strength <= 100:
            raise ValueError("Strength must be in [0, 100].")
        data, _ = load_gcode(args.input, args.plate)
        output, records = process(data, strength, args.bambu_times, mode=mode,
                                 jump_seconds=DEFAULT_JUMP_SECONDS if args.jump_seconds is None else args.jump_seconds,
                                 jump_percent=DEFAULT_JUMP_PERCENT if args.jump_percent is None else args.jump_percent,
                                 preferred_jump=5.0 if args.preferred_jump is None else args.preferred_jump,
                                 maximum_jump=8.0 if args.maximum_jump is None else args.maximum_jump,
                                 plateau_band=12.0 if args.plateau_band is None else args.plateau_band)
        write_gcode(args.output, [output.decode("utf-8")], "utf-8")
        if args.report:
            with args.report.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                columns = ["layer", "z_mm", "before_s", "at_max_speed_s", "target_s", "infill_feed_pct",
                           "after_estimated_s", "after_direct_s", "status", "toolchange_overhead_s",
                           "prime_tower_extrusion_before_s"]
                extra = ["global_strength", "feed_policy"] if mode != "legacy" else []
                if mode == "curve":
                    extra = ["smoothness", "feed_policy"]
                if mode in {"uniform", "plateau"}:
                    extra = ["feed_policy", *UNIFORM_COLUMNS]
                if mode == "plateau":
                    extra.extend(PLATEAU_COLUMNS)
                if len(records[0]) > len(columns) + len(extra):
                    columns.extend(AMS_COLUMNS)
                writer.writerow(columns + extra)
                writer.writerows(records)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
