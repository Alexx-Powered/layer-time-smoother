"""Plan layer times from the original and fastest attainable layer times only."""

import math


def _curved_run(times, adjustable, width):
    """Smooth in seconds, constrained to the nearby range of attainable times."""
    n = len(times)
    unit = max(times)
    floor = [t/unit for t in times]
    # Two maximum smoothing widths. A fixed cliff near the tip must not draw
    # an artificial high peak into an otherwise quick set of layers.
    ceilings = [max(floor[max(0,i-24):i+25])-v for i,v in enumerate(floor)]
    movable = [e and limit > 0 for e,limit in zip(adjustable,ceilings)]
    slope = width**2
    bend = width**4
    diag, near, far = [1.0]*n, [0.0]*n, [0.0]*n
    rhs = [0.0]*n
    for i in range(1, n):
        # Constant continuation outside the segment avoids extrapolating a
        # steep interior trend to an artificial peak on its first or last layer.
        weight = slope + bend * (int(i == 1) + int(i == n-1))
        diag[i-1] += weight
        diag[i] += weight
        near[i] -= weight
        delta = weight * (floor[i]-floor[i-1])
        rhs[i-1] += delta
        rhs[i] -= delta
    for i in range(2, n):
        diag[i-2] += bend
        diag[i-1] += 4*bend
        diag[i] += bend
        near[i-1] -= 2*bend
        near[i] -= 2*bend
        far[i] += bend
        delta = bend * (floor[i]-2*floor[i-1]+floor[i-2])
        rhs[i-2] -= delta
        rhs[i-1] += 2*delta
        rhs[i] -= delta

    def neighbor_sum(i, v):
        total = near[i]*v[i-1] if i else 0
        if i >= 2:
            total += far[i]*v[i-2]
        if i+1 < n:
            total += near[i+1]*v[i+1]
        if i+2 < n:
            total += far[i+2]*v[i+2]
        return total

    def solve(free, uplift):
        # Cholesky of the free rows and columns of the five-diagonal matrix.
        # Keeping locked coordinates as identity rows preserves its bandwidth.
        d, l1, l2, x = [1.0]*n, [0.0]*n, [0.0]*n, [0.0]*n
        locked = [0.0 if f else v for f,v in zip(free,uplift)]
        for i in range(n):
            if not free[i]:
                continue
            if i >= 2 and free[i-2]:
                l2[i] = far[i]/d[i-2]
            if i >= 1 and free[i-1]:
                l1[i] = (near[i]-l2[i]*l1[i-1])/d[i-1]
            d[i] = math.sqrt(diag[i]-l1[i]**2-l2[i]**2)
            x[i] = (rhs[i] - neighbor_sum(i, locked) - (l1[i]*x[i-1] if i else 0)
                    - (l2[i]*x[i-2] if i >= 2 else 0)) / d[i]
        for i in range(n-1, -1, -1):
            x[i] = (x[i] - (l1[i+1]*x[i+1] if i+1 < n else 0)
                    - (l2[i+2]*x[i+2] if i+2 < n else 0)) / d[i]
        return [a+b for a,b in zip(x,locked)]

    # Take the feasible fraction of each search step, stopping at either bound.
    free = movable.copy()
    uplift = [0.0]*n
    for _ in range(10*n+100):
        trial = solve(free, uplift)
        blockers = []
        for i in range(n):
            if not free[i]:
                continue
            if trial[i] < -1e-11 or trial[i] > ceilings[i]+1e-11:
                boundary = 0.0 if trial[i] < 0 else ceilings[i]
                fraction = (boundary-uplift[i])/(trial[i]-uplift[i])
                blockers.append((fraction,i,boundary))
        if blockers:
            fraction = min(blockers)[0]
            uplift = [min(limit,max(0.0,a+fraction*(b-a))) for a,b,limit in zip(uplift,trial,ceilings)]
            for crossing,i,boundary in blockers:
                if crossing <= fraction+1e-12:
                    free[i] = False
                    uplift[i] = boundary
            continue
        uplift = [min(limit,max(0.0,v)) for v,limit in zip(trial,ceilings)]
        best, release = 1e-9, None
        for i in range(n):
            if free[i] or not movable[i]:
                continue
            pull = rhs[i] - diag[i]*uplift[i] - neighbor_sum(i,uplift)
            if uplift[i] == ceilings[i]:
                pull = -pull
            if pull > best:
                best, release = pull, i
        if release is None:
            return [t+unit*v if v > 1e-12 else t for t,v in zip(times,uplift)]
        free[release] = True
    raise ValueError("Layer curvature fit did not converge.")


def curved_targets(fastest, editable, smoothness):
    """Fit closeness to fastest time, first differences and second differences."""
    if not math.isfinite(smoothness) or not 0 <= smoothness <= 100:
        raise ValueError("Smoothness must be between 0 and 100.")
    if len(fastest) != len(editable) or any(not math.isfinite(t) or t < 0 for t in fastest):
        raise ValueError("Expected aligned layers and finite nonnegative times.")
    result = fastest.copy()
    if not smoothness:
        return result
    width = 12 * (smoothness/100)**2
    # Disconnect first-layer adhesion and empty markers. Positive fixed layers
    # still anchor the free layers on either side.
    start = 1
    for end in range(1, len(result)+1):
        if end < len(result) and fastest[end] > 0:
            continue
        if end-start > 1:
            result[start:end] = _curved_run(fastest[start:end], editable[start:end], width)
        start = end+1
    return result


def bounded_curve(fastest, editable, steps):
    """Shortest componentwise feasible curve for local bounds on abs(delta log time).

    A low fixed layer can make a bound impossible: even the fastest taller layer
    may be too tall for the intervening steps. The required excess per edge is at
    least (height difference - sum of allowed steps) / distance. Fix the steepest
    forced ramp at this lower bound, then solve the remaining independent segments.
    """
    n = len(fastest)
    if not (len(editable) == len(steps) == n):
        raise ValueError("Layer arrays must align.")
    if any(not math.isfinite(t) or t <= 0 for t in fastest):
        raise ValueError("Relative layer smoothing needs positive finite layer estimates.")
    floor = list(map(math.log, fastest))
    fixed = [not e for e in editable]
    allowed = steps.copy()
    # Exclude first-layer adhesion time from both ends of every comparison.
    anchors = [i for i in range(1, n) if i == 1 or i == n - 1 or fixed[i]]
    segments = list(zip(anchors, anchors[1:]))
    while segments:
        left, right = segments.pop()
        if left == right:
            continue
        worst, ramp = 1e-12, None
        for anchor, end, direction in ((left, right, 1), (right, left, -1)):
            if not fixed[anchor]:
                continue
            allowance = 0.0
            for i in range(anchor + direction, end + direction, direction):
                allowance += steps[max(i, i - direction)]
                excess = (floor[i] - floor[anchor] - allowance) / abs(i - anchor)
                if excess > worst:
                    worst, ramp = excess, (anchor, i, direction)
        if ramp is None:
            continue
        anchor, end, direction = ramp
        value = floor[anchor]
        for i in range(anchor + direction, end + direction, direction):
            edge = max(i, i - direction)
            allowed[edge] = steps[edge] + worst
            value += allowed[edge]
            floor[i] = max(floor[i], value)
            fixed[i] = True
        segments.append((end, right) if direction == 1 else (left, end))
    for i in range(2, n):
        if not fixed[i]:
            floor[i] = max(floor[i], floor[i-1] - allowed[i])
    for i in range(n-2, 0, -1):
        if not fixed[i]:
            floor[i] = max(floor[i], floor[i+1] - allowed[i+1])
    curve = [f if not e or v <= math.log(f)+1e-12 else math.exp(v)
             for f, v, e in zip(fastest, floor, editable)]
    return curve, allowed


def uniform_targets(fastest, editable, preferred=5.0, maximum=8.0, *, original=None):
    """Use a tighter bound where it buys smoothness at low time cost."""
    if not (0 <= preferred <= maximum and math.isfinite(maximum)):
        raise ValueError("Require 0 <= preferred jump <= maximum jump (in percent).")
    n = len(fastest)
    if len(editable) != n or any(not math.isfinite(v) or v < 0 for v in fastest):
        raise ValueError("Expected aligned layer data and finite nonnegative time estimates.")
    if original is not None:
        if len(original) != n:
            raise ValueError("Original layer times must align with the speed limits.")
        jumps = sorted(relative_jump(original, i) for i in range(2, n) if min(original[i-1:i+1]) > 0)
        if len(jumps) >= 20:
            # On an already even print, keep ordinary ramps as tight as its own
            # upper-tail step. Do not tighten beyond the explicitly preferred bound.
            p95 = jumps[math.ceil(0.95 * len(jumps)) - 1]
            maximum = max(preferred, min(maximum, p95))
    tight_step, loose_step = (math.log1p(p/100) for p in (preferred, maximum))
    if 0 in fastest:
        # Empty layer markers have no relative duration. Keep them as separators.
        curve, requested, allowed = fastest.copy(), [preferred] * n, [tight_step] * n
        start = 1
        for end in range(1, n + 1):
            if end < n and fastest[end] > 0:
                continue
            if end > start:
                part, goals, bounds = uniform_targets([1.0] + fastest[start:end], [False] + editable[start:end],
                                                      preferred, maximum)
                curve[start:end], requested[start:end], allowed[start:end] = part[1:], goals[1:], bounds[1:]
            start = end + 1
        return curve, requested, allowed
    tight, tight_bounds = bounded_curve(fastest, editable, [tight_step]*n)
    if preferred == maximum:
        return tight, [preferred]*n, tight_bounds
    loose, _ = bounded_curve(fastest, editable, [loose_step]*n)
    steps = [tight_step]*n
    for i in range(2, n):
        premium = (tight[i-1] + tight[i]) / (loose[i-1] + loose[i]) - 1
        # Within 5% extra local time, keep the tight limit. At 20% extra, use the
        # loose limit. Interpolation is local, so a color change cannot relax a whole plate.
        weight = min(1.0, max(0.0, (premium-.05)/.15))
        steps[i] += weight * (loose_step-tight_step)
    curve, allowed = bounded_curve(fastest, editable, steps)
    return curve, [100*math.expm1(v) for v in steps], allowed


def relative_jump(times, i):
    return 100 * (max(times[i], times[i-1]) / min(times[i], times[i-1]) - 1)


def plateau_targets(uniform, editable, bounds, band_percent=12.0, half_wave=6):
    """Flatten short oscillations on the uniform curve, respecting its boundary steps."""
    if not math.isfinite(band_percent) or band_percent < 0 or not isinstance(half_wave, int) or half_wave < 1:
        raise ValueError("Expected a nonnegative plateau band and a positive half-wave length.")
    n = len(uniform)
    if len(editable) != n or len(bounds) != n:
        raise ValueError("Plateau times, editability and boundary limits must align.")
    # A sustained climb/descent is structure, including the tip of a long switch ramp.
    eligible = [i > 1 and editable[i-1] and editable[i] and min(uniform[i-1:i+1]) > 0
                for i in range(n)]
    run_start, direction = 2, 0
    for i in range(2, n + 1):
        delta = uniform[i] - uniform[i-1] if i < n and eligible[i] else 0
        sign = (delta > 1e-7) - (delta < -1e-7)
        if sign != direction or not sign:
            if direction and i - run_start > half_wave:
                eligible[run_start:i] = [False] * (i-run_start)
            run_start, direction = i, sign

    # Select disjoint oscillating intervals. Prefer more flattened edges, then
    # fewer plateaus and less extra time. Bounded search windows can merge later.
    scores = [(0, 0, 0.0)] * (n+1)
    starts = [None] * (n+1)
    for end in range(n):
        scores[end+1] = scores[end]
        low = high = total = uniform[end]
        variation = 0.0
        for start in range(end-1, max(0, end-8*half_wave), -1):
            if not eligible[start+1]:
                break
            first = uniform[start]
            total += first
            low, high = min(low, first), max(high, first)
            spread = high - low
            if spread > low * band_percent / 100 + 1e-10:
                break
            variation += abs(uniform[start+1]-first)
            if spread < 1e-6 or variation < 1.5*spread or variation-abs(uniform[end]-first) < spread:
                continue
            count = end-start+1
            edges, negative_groups, negative_cost = scores[start]
            score = (edges + count-1, negative_groups-1, negative_cost - (count*high-total))
            if score > scores[end+1]:
                scores[end+1], starts[end+1] = score, start
    groups = []
    pos = n
    while pos:
        start = starts[pos]
        if start is None:
            pos -= 1
        else:
            groups.append((start, pos-1))
            pos = start
    groups.reverse()
    merged = []
    for a, b in groups:
        if merged and a == merged[-1][1]+1 and eligible[a]:
            left = merged[-1][0]
            region = uniform[left:b+1]
            if max(region) <= min(region)*(1+band_percent/100) + 1e-10:
                merged[-1] = (left, b)
                continue
        merged.append((a,b))

    # Zero step inside a plateau means a shared time. Propagate across its
    # shoulders using the previous uniform mode's limits, including forced ramps.
    if not merged:
        return uniform.copy(), []
    base_steps = bounds.copy()
    for i in range(2, n):
        if min(uniform[i-1:i+1]) > 0:
            base_steps[i] = max(bounds[i], abs(math.log(uniform[i]/uniform[i-1])))

    def fit(selected):
        steps = base_steps.copy()
        for a, b in selected:
            steps[a+1:b+1] = [0.0] * (b-a)
        t = uniform.copy()
        for indices, offset in ((range(2,n), -1), (range(n-2,0,-1), 1)):
            for i in indices:
                j = i + offset
                if min(t[i],t[j]) <= 0:
                    continue
                required = t[j] * math.exp(-steps[max(i,j)])
                if not editable[i] and required > t[i] + 1e-8:
                    return None
                if editable[i]:
                    t[i] = max(t[i], required)
        return t

    times = fit(merged)
    if times is None:
        # Competing plateaus near a fixed endpoint may not fit simultaneously.
        accepted = []
        for interval in sorted(merged, key=lambda ab: ab[1]-ab[0], reverse=True):
            if fit(accepted + [interval]) is not None:
                accepted.append(interval)
        merged = sorted(accepted)
        times = fit(merged)
    return times, merged
