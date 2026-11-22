"""Build the Bambu zipapp and Orca wheel from the same local Python sources."""
import shutil
import csv
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

here = Path(__file__).resolve().parent
engine = here.parent / "工具"
modules = ("automatic_curve","gcode_feature_times","gcode_input","infill_speed_smoothing",
           "layer_time_smoothing","layer_uniformity","slicer_postprocess")

if __name__ == "__main__":
    bambu = here / "Bambu"
    bambu.mkdir(exist_ok=True)
    with zipfile.ZipFile(bambu/"layer_smoothing.pyz","w",compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("__main__.py","from slicer_postprocess import main\nmain()\n")
        for name in modules:
            bundle.write(engine/(name+".py"),name+".py")
    orca = here / "Orca"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)
        shutil.copy2(orca/"pyproject.toml",source)
        package = source/"orca_layer_smoothing"
        package.mkdir()
        shutil.copy2(orca/"orca_layer_smoothing"/"__init__.py",package)
        for asset in ("preview.html", "planner.js"):
            shutil.copy2(orca/"orca_layer_smoothing"/asset,package)
        examples = here.parent / "examples"
        with (examples/"stone-layers.csv").open(encoding="utf-8",newline="") as stream:
            layers = list(csv.DictReader(stream))
        with (examples/"stone-limits.csv").open(encoding="utf-8",newline="") as stream:
            limits = list(csv.DictReader(stream))
        assert [r["layer"] for r in layers] == [r["layer"] for r in limits]
        audit = json.loads((examples/"stone-audit.json").read_text(encoding="utf-8"))
        demo = dict(name="讲解石示例 · P2S", total=audit["original"]["total_s"],
                    before=[float(r["before_s"]) for r in layers], fastest=[float(r["fastest_s"]) for r in limits],
                    editable=[bool(int(r["editable"])) for r in limits], z=[float(r["z_mm"]) for r in layers])
        (package/"demo.json").write_text(json.dumps(demo,ensure_ascii=False),encoding="utf-8")
        for name in modules:
            shutil.copy2(engine/(name+".py"),package)
        subprocess.run([sys.executable,"-m","pip","--disable-pip-version-check","wheel","--no-index",
                        "--no-build-isolation","--no-deps","--wheel-dir",str(orca),str(source)],check=True)
