# Layer Time Smoother

通过调整**稀疏填充和内部实心填充的速度**，让相邻打印层的用时平缓过渡，用于研究 FDM 打印的冷却纹。Bambu Studio 版按盘自动选平滑度，OrcaSlicer 插件提供手动滑块。两者共用同一引擎，只调整模型填充的进给速度，保留首层、外墙与内墙、支撑、材料塔、换料和冲刷动作，无需新增暂停。

**[下载安装包](https://github.com/Alexx-Powered/layer-time-smoother/releases/tag/v0.2.0) · [安装与使用](#安装与使用) · [下载离线交互曲线](https://github.com/Alexx-Powered/layer-time-smoother/releases/download/v0.2.0/layer-time-explorer.html)**

## OrcaSlicer 2.5.1 实际导出

在 Windows 版 OrcaSlicer 中安装 GitHub 发布的 **0.2.0** 插件，用同一个讲解石模型分别导出原始、46 档和 90 档 G-code。本次使用 Bambu Lab A1、0.4 mm 喷嘴、Bambu PLA Basic 和 0.20 mm Standard 工艺，共 208 层。下表按导出的运动指令重新估时。整盘以 Orca 文件头的 55 分 35 秒为基准，叠加各层调速带来的时间差。A1 的条件延时摄影、喷头检测按未触发估算。

| 导出工艺 | 预计整盘用时 | 相对原盘 | 第 81 层 | 相邻层时间差 RMS |
|---|---:|---:|---:|---:|
| 原始 | 55 分 35 秒 | — | 9.20 秒 | 1.569 秒 |
| 平滑 46 | 57 分 25 秒 | +3.28% | 9.93 秒 | 0.958 秒 |
| 平滑 90 | 1 小时 5 分 45 秒 | +18.29% | 11.82 秒 | 0.891 秒 |

两档各有 205 层发生调速。逐条对齐三份 G-code，两种处理都只改变了 9,958 条稀疏填充和 24,878 条内部实心填充运动的进给，最高为原值的 150%。两档之间有 198 层的估时差超过 0.01 秒。图下半部放大了第 64–98 层，可看到 90 档把第 81 层附近的低谷填平。[208 层原始数据](examples/orca-stone-46-90.csv)

![讲解石原始、46 档和 90 档导出后的层时间曲线及局部放大](docs/orca-stone-export.png)

下面是装在 Orca 里的插件配置窗口。截图取自 90 档导出后，图例显示「导出重算」。第 105 层从 9.98 秒变为 12.07 秒，和单独读取最终 G-code 的数值一致。Orca 普通预览面板中的总时间可能仍沿用原文件头。[查看 46 档插件截图](docs/screenshots/orca-plugin-46.jpg)

![Orca 2.5.1 工艺插件配置页，90 档讲解石的导出重算曲线](docs/screenshots/orca-plugin-90.jpg)

## 三个实际切片

将原始与自动处理后的 G-code 分别导入 Bambu Studio 02.08.02.61，在「预览 → 颜色方案 → 层时间」截图。每组左右视角与缩放相同，各保留自己的秒数色标。下面把同一部位放大，并在图下用统一秒轴叠画原始和调速后的逐层曲线。表中用时由脚本从导出指令重新估算，冷却纹的表面效果仍需实物对照。[六张完整截图](docs/screenshots) · [逐层数据及选参报告](examples)

| 切片 | 层数 | 自动系数 | 层时间转折 RMS，前 → 后 | 整盘预计增时 |
|---|---:|---:|---:|---:|
| 讲解石 · P2S 单色 | 208 | 45 | 1.61 → 0.20 秒 | 2 分 53 秒（5.63%） |
| H2D · 双喷头，430 次切换 | 1,187 | 55 | 14.96 → 0.74 秒 | 1 小时 15 分 21 秒（10.23%） |
| 肥肥嘟 · P2S 单喷头，三色与异料支撑，89 次换料 | 327 | 40 | 44.01 → 5.91 秒 | 1 小时 22 分 40 秒（16.19%） |

**讲解石**　看模型下半部的细条带。处理后，相邻几层的颜色过渡更连续。

![讲解石相同视角的原始切片与自动 45 档层时间预览](docs/stone-detail.jpg)

**H2D**　顶部的一圈长层在原盘中很窄。调速后，邻层共同承担过渡，绿色区域变宽。

![H2D 双喷头原始切片与自动 55 档层时间预览](docs/h2d-detail.jpg)

**肥肥嘟**　整盘 8 件，图中放大同一区域。观察耳根和颈部换料层附近的色带。固定系数 65 预计多用 47.66%，本盘自动选择了 40。

![肥肥嘟同一区域的原始切片与自动 40 档层时间预览](docs/multicolor-detail.jpg)

## 安装与使用

### Bambu Studio · 自动选参

1. 安装 **Python 3.10 或更新版本**，下载 `layer_smoothing.pyz` 到固定目录。运行只使用 Python 标准库。[下载脚本](https://github.com/Alexx-Powered/layer-time-smoother/releases/download/v0.2.0/layer_smoothing.pyz)
2. 在终端运行 `python -c "import sys; print(sys.executable)"` 找到解释器。macOS / Linux 通常使用 `python3`。
3. 在 Bambu Studio 高级模式的「工艺 → 其他 → 后处理脚本」粘贴下面一行，把两处路径换成本机绝对路径，保留双引号。切片器会自动追加临时 G-code 文件名。

```text
"C:\Path\To\python.exe" "C:\SlicerTools\layer_smoothing.pyz"
```

4. 切片并导出 G-code，重新打开导出文件，选择「层时间」预览。发生调速时，文件内可搜索 `; infill_speed_smoothing policy=auto`，其中 `selected` 是选中系数，`plate_change` 是整盘估时变化。若原样获选，脚本保留原文件。

### OrcaSlicer · 手动滑块

1. 下载 wheel。在 Windows 版 **OrcaSlicer 2.5.1** 左上角，打开「文件」右侧的菜单图标 →「插件」。点右上「安装插件」左侧的小箭头，选择本地安装，选取 `.whl` 并激活。升级时停用旧版本。[下载实测版 0.2.0](https://github.com/Alexx-Powered/layer-time-smoother/releases/download/v0.2.0/orca_layer_smoothing-0.2.0-py3-none-any.whl)
2. 切到工艺的高级视图，打开「其他 → 切片流水线插件 → 添加插件」，选 **Layer Time Smoothing** 并确认。在下方「插件配置 → Configure…」打开层时间曲线。
3. 拖滑块或输入系数（0–100，步长 0.5，默认 65），点「保存」，关闭配置窗口，重新切片并导出。系数 0 让可调填充尽量提到原进给的 150%。系数越高，插件通常会把长短层之间的过渡摊到更多邻层。
4. 首次打开会显示有标记的内置讲解石示例。处理本盘后重新打开配置页，即可看最近一次处理的数据、预计整盘用时和相对变化。0.2.0 的全盘曲线从第 2 层开始，首层保持原速。选择上次使用的系数时显示「导出重算」，拖到其他系数时显示「调速预估」。点击曲线放大附近 31 层，悬停读单层秒数，也可以输入层号定位。

**Windows 2.5.1 权限实测。** 在插件管理器选中本插件，打开「权限」，为这个已核对来源的 wheel 启用「全部权限」，再重新切片。本机用这套配置完成了导出。0.2.0 使用临时文件描述符写回 G-code，Orca 可能弹出目标为空的 Python `open` 权限请求。全部权限是这次验证成功的配置，权限范围比读写当前切片更广。0.2.1 预发布版改用有路径的临时文件，本次桌面导出验证使用 0.2.0。[0.2.1 变更](https://github.com/Alexx-Powered/layer-time-smoother/releases/tag/v0.2.1)

本机另存工艺并重新打开时，还遇到过 Orca 提示「该预设存储的插件功能配置 JSON 无效」，界面回到默认 65。遇到此提示，请在配置页重新输入系数并保存，导出后在 G-code 中搜索 `infill_speed_smoothing policy=manual-` 核对实际数值。

OrcaSlicer 2.3.0 用户可使用 Bambu 版后处理脚本，在命令末尾追加 ` --smoothness 65`。[Orca 插件说明](https://www.orcaslicer.com/wiki/plugins_getting_started/)

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
