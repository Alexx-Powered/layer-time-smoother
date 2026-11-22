"""Keep existing slicer post-processing command lines working after moving the tools."""

import sys
from importlib import import_module
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "工具"))
implementation = import_module("工具.layer_time_smoothing")
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
