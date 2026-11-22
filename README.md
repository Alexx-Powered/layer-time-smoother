# Layer Time Smoother

通过调整**稀疏填充和内部实心填充的速度**，让相邻打印层的用时平缓过渡，用于研究 FDM 打印的冷却纹。Bambu Studio 版按盘自动选平滑度，OrcaSlicer 插件提供手动滑块。两者共用同一引擎，只调整模型填充的进给速度，保留首层、外墙与内墙、支撑、材料塔、换料和冲刷动作，无需新增暂停。

**[下载安装包](https://github.com/Alexx-Powered/layer-time-smoother/releases/tag/v0.1.0) · [安装与使用](#安装与使用) · [下载离线交互曲线](https://github.com/Alexx-Powered/layer-time-smoother/releases/download/v0.1.0/layer-time-explorer.html)**

## 三个实际切片

将原始与自动处理后的 G-code 分别导入 Bambu Studio 02.08.02.61，在「预览 → 颜色方案 → 层时间」截图。每组左右视角与缩放相同，各保留自己的秒数色标。表中用时由脚本从导出指令重新估算，冷却纹的表面效果仍需实物对照。[六张完整截图](docs/screenshots) · [逐层数据及选参报告](examples)

| 切片 | 层数 | 自动系数 | 层时间转折 RMS，前 → 后 | 整盘预计增时 |
|---|---:|---:|---:|---:|
| 讲解石 · P2S 单色 | 208 | 45 | 1.61 → 0.20 秒 | 2 分 53 秒（5.63%） |
| H2D · 双喷头，430 次切换 | 1,187 | 55 | 14.96 → 0.74 秒 | 1 小时 15 分 21 秒（10.23%） |
| 肥肥嘟 · P2S 单喷头，三色与异料支撑，89 次换料 | 327 | 40 | 44.01 → 5.91 秒 | 1 小时 22 分 40 秒（16.19%） |

**讲解石**　看模型下半部的细条带。处理后，相邻几层的颜色过渡更连续。

![讲解石相同视角的原始切片与自动 45 档层时间预览](docs/stone-preview.jpg)

**H2D**　顶部的一圈长层在原盘中很窄。调速后，邻层共同承担过渡，绿色区域变宽。

![H2D 双喷头原始切片与自动 55 档层时间预览](docs/h2d-preview.jpg)

**肥肥嘟**　整盘 8 件，图中放大同一区域。观察耳根和颈部换料层附近的色带。固定系数 65 预计多用 47.66%，本盘自动选择了 40。

![肥肥嘟同一区域的原始切片与自动 40 档层时间预览](docs/multicolor-preview.jpg)

## 安装与使用

### Bambu Studio · 自动选参

1. 安装 **Python 3.10 或更新版本**，下载 `layer_smoothing.pyz` 到固定目录。运行只使用 Python 标准库。[下载脚本](https://github.com/Alexx-Powered/layer-time-smoother/releases/download/v0.1.0/layer_smoothing.pyz)
2. 在终端运行 `python -c "import sys; print(sys.executable)"` 找到解释器。macOS / Linux 通常使用 `python3`。
3. 在 Bambu Studio 高级模式的「工艺 → 其他 → 后处理脚本」粘贴下面一行，把两处路径换成本机绝对路径，保留双引号。切片器会自动追加临时 G-code 文件名。

```text
"C:\Path\To\python.exe" "C:\SlicerTools\layer_smoothing.pyz"
```

4. 切片并导出 G-code，重新打开导出文件，选择「层时间」预览。发生调速时，文件内可搜索 `; infill_speed_smoothing policy=auto`，其中 `selected` 是选中系数，`plate_change` 是整盘估时变化。若原样获选，脚本保留原文件。

### OrcaSlicer · 手动滑块

1. 在 **OrcaSlicer 2.5.1** 的「文件 → 插件 → 安装本地插件」选择下载的 `.whl`，启用 **Layer Time Smoothing**。[下载插件](https://github.com/Alexx-Powered/layer-time-smoother/releases/download/v0.1.0/orca_layer_smoothing-0.1.0-py3-none-any.whl)
2. 在工艺的 G-code 后处理插件列表中勾选 **Layer Time Smoothing**。在插件配置页调节 0–100 的滑块，点「保存系数」，再切片导出。默认 65，步长 0.5。系数 0 让可调填充尽量提速到原进给的 150%，系数越高，长短层之间的过渡通常越宽。
3. **OrcaSlicer 2.3.0** 可用上一节的后处理脚本，在命令末尾追加 ` --smoothness 65`。2.3.0 已用真实切片验证。2.5.1 原生版已验证插件回调，尚待该版本的图形界面安装验证。[Orca 插件说明](https://www.orcaslicer.com/wiki/plugins_getting_started/)

### 先用自己的文件试一盘

在脚本所在目录运行下列命令，会生成独立的 G-code 和包含各档评分的 JSON。输入支持带换层、走线类型和运动参数注释的 Bambu/Orca 平面 G-code，以及仅含一盘 G-code 的 `.gcode.3mf`。

```sh
python layer_smoothing.pyz input.gcode --output preview.gcode --audit decision.json
```

查看 `exported.total_s` 和 `exported.change_pct` 可得整盘新估时和相对变化。切片文件头和 `M73` 进度指令保留原切片值。也可以下载页首的离线交互曲线，用浏览器打开，拖动系数或悬停检查三个样本的逐层细节。

## 自动模式怎样取舍

从第 2 层起，同时衡量**相邻层的时间差**和**连续三层的转折**。例如 12、13、14 秒是匀速上坡，转折为零；12、14、12 秒的转折幅度为 4 秒。两项各取均方根（RMS），按原盘归一化并等权计分，再加上整盘耗时增幅。脚本比较原盘与 0–100 间每隔 5 的 21 档候选，选取合格候选中得分最低的一档，写出后重新估时验收。

自动模式把整盘预计用时限制在原盘的 150% 以内。填充指令的进给上限是原值的 150%，遇到极长换料层时，局部填充也可能大幅减速。当前版本按进给倍率限制速度，尚未按材料最大体积流量另行限速。[评分实现](工具/automatic_curve.py)

## 从源码运行

```sh
python -m unittest discover -s 工具 -p "test*.py"
python 工具/slicer_postprocess.py input.gcode --output preview.gcode --audit decision.json
python 工具/compare_layers.py input.gcode preview.gcode layers.csv
```

逐层 CSV 的一行对应一层。讲解石第 2 层的 `z_mm` 为 0.4 毫米，`before_s` 为 25.77543 秒，`after_s` 为 22.59523 秒。[讲解石逐层 CSV](examples/stone-layers.csv)

用 `python 工具/build_layer_explorer.py` 可从公开数据重建交互曲线。重建两个安装包需要 `setuptools>=61` 和 `wheel`，安装后运行 `python 集成/build.py`，产物分别在 `集成/Bambu/` 和 `集成/Orca/`。
