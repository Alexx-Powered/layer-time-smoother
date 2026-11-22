"""OrcaSlicer plugin entry point."""
import json

import orca

from .slicer_postprocess import run_job


class LayerTime(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Layer Time Smoothing"

    def get_default_config(self):
        return {"smoothness": 65}

    def has_config_ui(self):
        return True

    def get_config_ui(self):
        return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<style>
:root {color-scheme:light dark;font:16px system-ui,sans-serif}
body {margin:16px;max-width:32em} label {display:block} input {width:100%;margin:14px 0}
button {padding:6px 14px;font:inherit} output {font-variant-numeric:tabular-nums}
</style><label for="strength">层时间平滑系数　<output id="value" for="strength"></output></label>
<input type="range" min="0" max="100" step="0.5" id="strength">
<button type="button" id="save">保存系数</button>
<script>
const stored=window.orca.getConfig();
const settings=typeof stored==='string'?JSON.parse(stored):stored;
const slider=document.getElementById('strength'), value=document.getElementById('value');
slider.value=settings?.smoothness??65;
function display(){value.textContent=slider.value}
display();slider.addEventListener('input',display);
document.getElementById('save').onclick=()=>window.orca.saveConfig({smoothness:slider.valueAsNumber});
</script></html>"""

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.psGCodePostProcess:
            return orca.ExecutionResult.success()
        try:
            config = json.loads(self.get_config() or "{}")
            strength = float(config.get("smoothness",65))
            return orca.ExecutionResult.success(run_job(ctx.gcode_path,strength))
        except (OSError,ValueError,TypeError,AttributeError) as exc:
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError,str(exc))


@orca.plugin
class LayerSmoothingPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(LayerTime)
