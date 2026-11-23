"""Glue shared by the static demo worker and the precomputed Voron example."""
import math

from automatic_curve import select_curve
from infill_speed_smoothing import INFILL, prepare_input
from layer_uniformity import curved_targets
from slicer_postprocess import smooth_bytes


class BrowserMotionCache:
    def __init__(self, cache):
        self.cache = cache

    def record(self, layer, role, length, a, feed, factor, speed_limit, junction_limit, start):
        editable = layer > 0 and role in INFILL
        self.cache.add(layer, editable, length, a, feed, factor, speed_limit, junction_limit, start)

    def finish(self, baseline):
        from pyodide.ffi import to_js
        self.cache.finish(to_js(baseline))

    def simulate(self, scales):
        from pyodide.ffi import to_js
        return list(self.cache.times(to_js(scales)).to_py())


class Preview:
    def __init__(self, source, cache=None):
        self.source = source
        self.prepared = prepare_input(source, motion_cache=BrowserMotionCache(cache) if cache is not None else None)
        _, _, self.heights, _, _, _, self.before, self.editable, _, self.fastest = self.prepared
        self.plan, self.decision = select_curve(source, self.before, self.fastest, self.editable)
        self.total = self.decision["original"]["total_s"]
        self.current = None
        self.output = None

    def planning_inputs(self):
        return dict(before=self.before, heights=self.heights, beforeTotal=self.total,
                    fastest=self.fastest, editable=self.editable,
                    autoStrength=self.decision["planned"]["strength"])

    def summary(self, strength, selected, after, changed, total):
        return dict(strength=strength, selected=selected, before=self.before, after=after,
                    heights=self.heights, beforeTotal=self.total, afterTotal=total, changed=changed)

    def preview(self, strength):
        if strength is None:
            selected = self.decision["planned"]["strength"]
            after = self.plan
        else:
            selected = strength
            after = curved_targets(self.fastest, self.editable, strength)
        total = self.total + (math.fsum(after) - math.fsum(self.before))
        return self.summary(strength, selected, after, after != self.before, total)

    def calculate(self, strength):
        if self.current is not None and self.current["strength"] == strength:
            return self.current
        self.output = None
        self.current = None
        output, rows, report, _ = smooth_bytes(self.source, strength, prepared=self.prepared)
        if report is not None:
            selected = report["exported"]["strength"]
            total = report["exported"]["total_s"]
        else:
            selected = strength
            total = self.total + math.fsum(r[6] - r[2] for r in rows)
        after = [r[6] for r in rows] if rows else self.before
        changed = output != self.source
        self.current = self.summary(strength, selected, after if changed else self.before, changed,
                                    total if changed else self.total)
        self.output = output
        return self.current
