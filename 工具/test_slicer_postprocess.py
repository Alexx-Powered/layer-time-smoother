import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automatic_curve import original_total, rate_curve, roughness, select_curve
from gcode_feature_times import estimate
from slicer_postprocess import run_job


def sample():
    lines = ["; model printing time: 50s; total estimated time: 1m 3s", "M201 X1000 Y1000 Z100 E1000",
             "M203 X200 Y200 Z10 E200", "M204 S100 T100", "M205 X10 Z1", "M83"]
    for i, dwell in enumerate((4000,1000,4000,1000,4000,1000,4000,1000)):
        lines += [";LAYER_CHANGE", f";Z:{.2*(i+1):.1f}", ";TYPE:Sparse infill",
                  f"G1 X{20*(i+1)} E1 F1200", f"G4 P{dwell}"]
    return ("\r\n".join(lines)+"\r\n").encode()


class SlicerPostprocessTest(unittest.TestCase):
    def test_a1_optional_recording_and_unknown_conditions(self):
        prefix = b"; printer_model = Bambu Lab A1\n"
        block = (b"M624 AQAAAAAAAAA=\n; SKIPPABLE_START\n; SKIPTYPE: timelapse\n"
                 b"M622 J1\nG1 X50 F1800\nM623\n; SKIPPABLE_END\nM625\n")
        source = prefix + sample()
        heights, original, _ = estimate(source)
        _, modified, notes = estimate(source + block)
        self.assertEqual(original, modified)
        self.assertFalse(any(notes))
        _, _, unknown = estimate(source + block + b"M622 J1\nG1 X50 F1800\nM623\n")
        self.assertEqual(unknown[-1], {"conditional G1"})

    def test_fixed_flat_curve_keeps_original_and_skips_empty_boundaries(self):
        t = [900,10,10,10,0,40,40]
        _, report = select_curve(b"",t,t,[False]+[True]*6)
        self.assertIsNone(report["planned"]["strength"])
        self.assertEqual(roughness(t),(0,0))
        self.assertEqual(report["original"]["total_source"],"layer_sum")
        self.assertEqual(original_total(sample(),t),(63,"gcode_header"))

    def test_time_cap_and_roughness_are_independent(self):
        base = dict(total_s=100,layer_sum_s=100,jump_rms_s=2,bend_rms_s=3)
        self.assertFalse(rate_curve(100,[20,50,50,50],base)["eligible"])
        self.assertFalse(rate_curve(0,[20,10,20,10],base)["eligible"])
        self.assertTrue(rate_curve(40,[20,20,21,22],base)["eligible"])

    def test_in_place_reentry_unsupported_file_and_atomic_failure(self):
        for strength in (None,37.5):
            with self.subTest(strength=strength):
                with tempfile.NamedTemporaryFile(prefix="层时间 test ",suffix=".pp",delete=False) as f:
                    f.write(sample())
                    path=Path(f.name)
                try:
                    with patch("slicer_postprocess.os.replace",side_effect=OSError("write failed")):
                        with self.assertRaises(OSError):
                            run_job(path,strength)
                    self.assertEqual(path.read_bytes(),sample())
                    run_job(path,strength)
                    rendered = path.read_bytes()
                    self.assertEqual(rendered.count(b"; infill_speed_smoothing "),1)
                    self.assertIn(b"; infill_speed_smoothing policy=",rendered)
                    self.assertIn("Already applied",run_job(path,strength))
                    self.assertEqual(rendered,path.read_bytes())
                    self.assertEqual(rendered.count(b"G4 "),sample().count(b"G4 "))
                    path.write_bytes(b"not a supported sliced file")
                    with self.assertRaises(ValueError):
                        run_job(path,strength)
                    self.assertEqual(path.read_bytes(),b"not a supported sliced file")
                finally:
                    path.unlink(missing_ok=True)
