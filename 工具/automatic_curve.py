"""Rank time curves using only the original, planned and exported layer durations."""
import math

if __package__:
    from .gcode_input import header_total_seconds
    from .layer_uniformity import curved_targets
else:
    from gcode_input import header_total_seconds
    from layer_uniformity import curved_targets


def original_total(data, baseline):
    total = header_total_seconds(data)
    if total is not None:
        return total, "gcode_header"
    return math.fsum(baseline), "layer_sum"


def roughness(times):
    """RMS of adjacent jumps and three-layer bends, starting at layer 2."""
    jumps = [b-a for a,b in zip(times[1:],times[2:]) if min(a,b)>0]
    bends = [c-2*b+a for a,b,c in zip(times[1:],times[2:],times[3:]) if min(a,b,c)>0]
    def rms(v):
        return math.sqrt(math.fsum(x*x for x in v)/len(v)) if v else 0.0
    return rms(jumps), rms(bends)


def rate_curve(strength, times, original):
    jump, bend = roughness(times)
    total = original["total_s"] + math.fsum(times) - original["layer_sum_s"]
    delta = (total-original["total_s"])/original["total_s"] if original["total_s"] else 0.0
    score = .5*jump/max(.25,original["jump_rms_s"]) + .5*bend/max(.25,original["bend_rms_s"]) + delta
    allowed = (0 <= total <= 1.5*original["total_s"] + 1e-8 and
               jump <= original["jump_rms_s"] + .01 and bend <= original["bend_rms_s"] + .01)
    return dict(strength=strength,total_s=total,change_pct=100*delta,jump_rms_s=jump,
                bend_rms_s=bend,score=score,eligible=allowed)


def select_curve(data, baseline, fastest, editable):
    total, source = original_total(data, baseline)
    jump, bend = roughness(baseline)
    original = dict(total_s=total, total_source=source, layer_sum_s=math.fsum(baseline),
                    jump_rms_s=jump,bend_rms_s=bend)
    unchanged = rate_curve(None, baseline, original)
    winner, targets = unchanged, baseline
    candidates = [unchanged]
    if any(editable):
        for strength in range(0,101,5):
            curve = curved_targets(fastest,editable,strength)
            c = rate_curve(strength,curve,original)
            candidates.append(c)
            if c["eligible"] and c["score"] < winner["score"]-1e-9:
                winner, targets = c, curve
    return targets, dict(original=original,candidates=candidates,planned=winner)
