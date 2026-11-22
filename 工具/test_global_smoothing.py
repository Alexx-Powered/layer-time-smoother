"""Regression checks for the plate-time policies (run with python -m unittest)."""

import math
import random
import unittest

from gcode_feature_times import estimate
from layer_uniformity import bounded_curve, curved_targets, plateau_targets, relative_jump, uniform_targets
from infill_speed_smoothing import (CAP, MAX_TIME_RATIO, TIME_TOLERANCE, budget_curve, enforce_time_cap,
                                   global_targets, process, reconcile_total, speed_first_targets)


class GlobalSmoothingTest(unittest.TestCase):
    def test_curve_strength_and_fixed_layers(self):
        f = [800, 10, 10, 12, 10, 11, 10, 13, 10, 10, 0, 7, 7]
        editable = [True]*len(f)
        editable[6] = False
        self.assertEqual(curved_targets(f, editable, 0), f)
        def bend(times):
            y = times[1:10]
            return sum((a-2*b+c)**2 for a,b,c in zip(y,y[1:],y[2:]))
        weaker = curved_targets(f, editable, 30)
        stronger = curved_targets(f, editable, 75)
        self.assertLess(bend(stronger), bend(weaker))
        self.assertLess(bend(weaker), bend(f))
        self.assertTrue(all(t >= floor for t,floor in zip(stronger,f)))
        self.assertEqual([stronger[i] for i in (0,6,10,11,12)], [800,10,0,7,7])
        self.assertGreater(len(set(stronger[2:6])), 2)

    def test_curve_does_not_invent_a_peak_before_a_fixed_cliff(self):
        f = [900, 30] + [12, 11]*30 + [11, 6]
        t = curved_targets(f, [False]+[True]*61+[False,False], 95)
        self.assertEqual(t[-2:], f[-2:])
        self.assertLessEqual(max(t[30:-2]), 12.000001)
        self.assertLessEqual(max(t[1:]), 30.000001)

    def test_curve_bounded_minimum(self):
        # Check the gradient of the objective independently. Free coordinates
        # have zero gradient and lower-bound coordinates may only rise.
        rng = random.Random(4)
        for _ in range(12):
            f = [1.0]+[rng.uniform(8,35) for _ in range(20)]
            editable = [False]+[rng.random() > .18 for _ in f[1:]]
            width = 12*.55**2
            t = curved_targets(f, editable, 55)
            x = t[1:]
            g = [v-lo for v,lo in zip(x,f[1:])]
            for i,(a,b) in enumerate(zip(x,x[1:])):
                v = (b-a)*(width**2+width**4*(int(i==0)+int(i==len(x)-2)))
                g[i] -= v
                g[i+1] += v
            for i,(a,b,c) in enumerate(zip(x,x[1:],x[2:])):
                v = (a-2*b+c)*width**4
                g[i] += v
                g[i+1] -= 2*v
                g[i+2] += v
            for i,pull in enumerate(g,1):
                if not editable[i]:
                    self.assertEqual(t[i], f[i])
                elif max(f[1:])-f[i] < 1e-9:
                    self.assertAlmostEqual(t[i], f[i])
                elif t[i] >= max(f[1:])-1e-9:
                    self.assertLessEqual(pull, 1e-7)
                elif t[i] > f[i]+1e-9:
                    self.assertAlmostEqual(pull, 0, places=7)
                else:
                    self.assertGreaterEqual(pull, -1e-8)

    def test_curve_from_serialized_feed(self):
        lines = ["M201 X1000 Y1000 Z100 E1000", "M203 X200 Y200 Z10 E200",
                 "M204 S100 T100", "M205 X10 Z1", "M83"]
        for i, dwell in enumerate((4000, 1000, 1300, 2200, 1500, 1000, 1100)):
            lines += ["; CHANGE_LAYER", f"; Z_HEIGHT: {.2*(i+1):.1f}", "; FEATURE: Sparse infill",
                      f"G1 X{20*(i+1)} E1 F1200", f"G4 P{dwell}"]
        source = ("\n".join(lines)+"\n").encode()
        out, report = process(source, 65, mode="curve")
        _, actual, _ = estimate(out)
        self.assertEqual(out.count(b"G4 "), source.count(b"G4 "))
        for row, layer in zip(report,actual):
            self.assertLess(abs(row[4]-layer.total()), .05)
            self.assertLessEqual(row[5], 150.)
            self.assertEqual(row[-2],65)

    def test_long_alternating_run_is_one_level(self):
        curve = [80] + [10, 10.4]*65
        t, groups = plateau_targets(curve, [False]+[True]*130, [math.log(1.05)]*131)
        self.assertEqual(groups, [(1,130)])
        self.assertEqual(t, [80]+[10.4]*130)

    def test_long_ramps_fixed_layers_and_large_steps(self):
        ramp = [80]+list(range(100,111))+list(range(109,95,-1))
        t, groups = plateau_targets(ramp, [False]+[True]*(len(ramp)-1), [math.log(1.05)]*len(ramp))
        self.assertEqual((t,groups), (ramp,[]))
        curve = [80,10,10.2,10,0,20,21,20,21,20,40,20]
        editable = [False,True,True,True,False,True,False,True,True,True,True,False]
        t, groups = plateau_targets(curve, editable, [math.log(2)]*len(curve))
        self.assertEqual(groups, [(1,3),(7,9)])
        self.assertEqual(t, [80,10.2,10.2,10.2,0,20,21,21,21,21,40,20])

    def test_no_flattening_across_infeasible_fixed_shoulder(self):
        curve = [80,10,10.5,11,10.5]
        t, groups = plateau_targets(curve, [False,False,True,True,True], [math.log(1.05)]*5)
        self.assertEqual(groups, [])
        self.assertEqual(t,curve)

    def test_actual_plateau_from_written_gcode(self):
        lines = ["M201 X1000 Y1000 Z100 E1000", "M203 X200 Y200 Z10 E200",
                 "M204 S100 T100", "M205 X10 Z1", "M83"]
        for i in range(9):
            lines += ["; CHANGE_LAYER", f"; Z_HEIGHT: {.2*(i+1):.1f}", "; FEATURE: Sparse infill",
                      f"G1 X{20*(i+1)} E1 F1200", f"G4 P{5000+200*(i%2)}"]
        source = ("\n".join(lines)+"\n").encode()
        output, report = process(source, 100, mode="plateau")
        _, reread, _ = estimate(output)
        times = [row.total() for row in reread[1:]]
        self.assertTrue(all(row[-3] == 1 for row in report[1:]))
        self.assertLessEqual(max(times)-min(times), .01)
        self.assertEqual(output.count(b"G4 "), source.count(b"G4 "))

    def test_uniform_short_layers_do_not_get_three_second_loophole(self):
        f = [900, 5, 7, 5]
        t, requested, bounds = uniform_targets(f, [False, True, True, True], 5, 5)
        self.assertEqual(t[0], 900)
        self.assertEqual(t[2], 7)
        self.assertAlmostEqual(t[1], 7/1.05)
        self.assertAlmostEqual(t[3], 7/1.05)
        self.assertEqual(requested, [5]*4)
        self.assertAlmostEqual(bounds[-1], math.log(1.05))

    def test_uniform_immutable_endpoint_spreads_only_unavoidable_jump(self):
        f = [99, 10, 40, 10, 10]
        t, _, bound = uniform_targets(f, [False,True,True,True,False], 5, 5)
        self.assertAlmostEqual(t[1], 40/1.05)
        self.assertEqual(t[2],40)
        self.assertAlmostEqual(t[3],20)
        self.assertEqual(t[4],10)
        self.assertAlmostEqual(bound[2],math.log(1.05))
        self.assertAlmostEqual(bound[3],math.log(2))
        self.assertAlmostEqual(bound[4],math.log(2))
        self.assertGreater(sum(t[1:]),1.5*sum(f[1:]))

    def test_uniform_limits_are_local_and_satisfied(self):
        rng = random.Random(481)
        for _ in range(200):
            n = rng.randint(1,60)
            f = [rng.uniform(3,200) for _ in range(n)]
            editable = [False] + [rng.random()<.8 for _ in range(n-1)]
            t, requested, bound = uniform_targets(f, editable)
            self.assertTrue(all(5-1e-8 <= r <= 8+1e-8 for r in requested))
            for i in range(n):
                self.assertGreaterEqual(t[i]+1e-8, f[i])
                if not editable[i]:
                    self.assertEqual(t[i], f[i])
                if i >= 2:
                    self.assertLessEqual(relative_jump(t,i), 100*math.expm1(bound[i])+1e-7)
        self.assertEqual(bounded_curve([30,10], [False,True], [0,0])[0], [30,10])
        with self.assertRaisesRegex(ValueError, 'preferred jump'):
            uniform_targets([10,10], [False,True], 9, 5)
        empty = [0, 6, 0, 60, 30]
        curve, _, _ = uniform_targets(empty, [False, True, False, True, True], 5, 5)
        self.assertEqual(curve[:4], empty[:4])
        self.assertAlmostEqual(curve[4], 60/1.05)
        # One isolated peak cannot loosen the ordinary ramps of an otherwise flat print.
        profile = [10.] * 50
        profile[25] = 100.
        _, requested, _ = uniform_targets(profile, [False]+[True]*49, original=profile)
        self.assertTrue(all(abs(step-5) < 1e-8 for step in requested))

    def test_quiet_plate_keeps_all_speed_savings(self):
        fastest = [700, 20, 23, 20, 19, 18]
        baseline = [700, 24, 26, 22, 23, 22]
        strength, t = speed_first_targets(baseline, fastest, [False]+[True]*5, 3, 20)
        self.assertEqual(strength, 0)
        self.assertEqual(t, fastest)
        self.assertLess(sum(t), sum(baseline))

    def test_speed_first_uses_only_needed_time(self):
        fast = [789, 10, 24, 10, 10, 10]
        strength, t = speed_first_targets(fast, fast, [False, True, False, True, True, True], 3, 50)
        self.assertEqual(strength, 100)
        self.assertEqual(t[:4], [789, 16, 24, 16])
        self.assertAlmostEqual(t[4], 16/1.5)
        self.assertEqual(t[-1], 10)
        self.assertLess(sum(t), MAX_TIME_RATIO * sum(fast))

    def test_unavoidable_fixed_jump_does_not_spend_budget(self):
        f = [5, 5, 5, 100]
        self.assertEqual(speed_first_targets(f, f, [False, True, False, False], 3, 20), (0, f))

    def test_serialized_cap_rounding_and_infeasibility(self):
        # Error budget is relative to the source; only the compensating feed moves.
        rates = [1, CAP, .4]
        def readback(r):
            return (math.ceil(80 / r[-1]) / 10,)
        corrected, values = enforce_time_cap(rates, [False, True, True], readback, readback(rates), 19.9)
        self.assertLessEqual(values[-1], 19.9)
        self.assertEqual(corrected[:2], rates[:2])
        with self.assertRaisesRegex(ValueError, 'exceeds 150%'):
            enforce_time_cap(rates, [False, True, True], readback, readback(rates), 4)

    def test_real_gcode_hits_cap_only_for_extreme_jump(self):
        lines = ["M201 X1000 Y1000 Z100 E1000", "M203 X200 Y200 Z10 E200",
                 "M204 S100 T100", "M205 X10 Z1", "M83"]
        for i in range(7):
            lines.extend(["; CHANGE_LAYER", f"; Z_HEIGHT: {0.2*(i+1):.1f}", "; FEATURE: Sparse infill",
                          f"G1 X{(i+1)*20} E1 F1200"])
            if i == 3:
                lines.append("G4 S120")
        data = ("\n".join(lines) + "\n").encode()
        _, before, _ = estimate(data)
        output, records = process(data, 100, mode="auto")
        _, after, _ = estimate(output)
        ratio = sum(r.total() for r in after) / sum(r.total() for r in before)
        self.assertLessEqual(ratio, MAX_TIME_RATIO)
        self.assertGreater(ratio, 1.499)
        self.assertTrue(0 < records[0][-2] < 100)
        self.assertEqual(output.count(b"G4 S120"), 1)
        self.assertEqual(records[0][5], 100)
        uniform, records = process(data, 100, mode="uniform")
        self.assertEqual(uniform.count(b"G4 S120"), 1)
        self.assertGreater(sum(row[6] for row in records), MAX_TIME_RATIO * sum(row[2] for row in records))
        self.assertTrue(all(row[-4] <= row[-2] + .02 for row in records[2:]))

    def test_ordinary_layers_and_isolated_first(self):
        fast = [999, 10, 10, 10, 20, 10, 10, 10]
        editable = [False] + [True] * 7
        baseline = [v + int(e) for v, e in zip(fast, editable)]
        s, t = global_targets(baseline, fast, editable)
        self.assertTrue(0 < s < 100)
        self.assertAlmostEqual(sum(t), sum(baseline))
        self.assertEqual(t[1], 10)
        self.assertEqual(t[-1], 10)
        self.assertGreater(t[3], 10)
        self.assertAlmostEqual(t[3], t[5])
        self.assertEqual(global_targets(baseline, fast, editable, s)[1], t)
        self.assertEqual(budget_curve(fast, editable, 0)[1:], [20] * 7)

    def test_flat_plate_and_no_infill(self):
        v = [70, 10, 10]
        self.assertEqual(global_targets(v, v, [False]*3), (0, v))
        with self.assertRaisesRegex(ValueError, "ordinary infill"):
            global_targets([70, 12, 12], v, [False, True, True])

    def test_curves_satisfy_bound_and_neighbor_equations(self):
        rng = random.Random(680)
        for _ in range(100):
            f = [rng.uniform(3, 100) for _ in range(rng.randint(3, 45))]
            e = [False] + [rng.random() < .8 for _ in f[1:]]
            p = rng.choice([0, 1, 10, 100])
            t = budget_curve(f, e, p)
            for i, editable in enumerate(e):
                if not editable:
                    self.assertEqual(t[i], f[i])
                    continue
                self.assertGreaterEqual(t[i] + 1e-8, f[i])
                pull = sum(t[j] - t[i] for j in (i-1, i+1) if 1 <= j < len(t))
                self.assertLessEqual(pull, p + 1e-8)
                if t[i] > f[i] + 1e-8:
                    self.assertAlmostEqual(pull, p)

    def test_serialized_correction_changes_only_compensation(self):
        rates = [1, CAP, .8]
        for goal in (9.0, 16.0):
            def measure(r):
                return (10/r[2] - goal,)
            result, (error,) = reconcile_total(rates, [False, False, True], measure, measure(rates))
            self.assertEqual(result[:2], rates[:2])
            self.assertLessEqual(abs(error), TIME_TOLERANCE)
            self.assertTrue(math.isfinite(result[2]) and 0 < result[2] <= CAP)


if __name__ == "__main__":
    unittest.main()
