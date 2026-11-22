"""OrcaSlicer slicing capability and its embedded layer-time viewer."""
import json
from pathlib import Path

import orca

from .automatic_curve import original_total
from .slicer_postprocess import atomic_write, run_job


def preview_path():
    return Path(orca.host.plugin.storage()) / "last-export.json"


class LayerTime(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Layer Time Smoothing"

    def get_default_config(self):
        return {"smoothness": 65}

    def has_config_ui(self):
        return True

    def get_config_ui(self):
        package = Path(__file__).parent
        try:
            payload = json.loads(preview_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, RuntimeError):
            payload = json.loads((package / "demo.json").read_text(encoding="utf-8"))
        html = (package / "preview.html").read_text(encoding="utf-8")
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
        return html.replace("__PLANNER__", (package / "planner.js").read_text(encoding="utf-8")).replace("__DATA__", data)

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.psGCodePostProcess:
            return orca.ExecutionResult.success()
        try:
            settings = json.loads(self.get_config() or "{}")
            strength = float(settings.get("smoothness", 65))
            def remember(data, rows):
                before = [r[2] for r in rows]
                total, _ = original_total(data, before)
                payload = dict(name=Path(ctx.output_name).name, total=total, strength=strength,
                               before=before, z=[r[1] for r in rows], fastest=[r[3] for r in rows],
                               editable=[r[-1] != "protected" for r in rows], exported=[r[6] for r in rows])
                atomic_write(preview_path(), json.dumps(payload, allow_nan=False).encode("utf-8"))
            message = run_job(ctx.gcode_path, strength, on_preview=remember)
            return orca.ExecutionResult.success(message)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, str(exc))


@orca.plugin
class LayerSmoothingPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(LayerTime)
