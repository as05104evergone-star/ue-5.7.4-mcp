# -*- coding: utf-8 -*-
r"""
重采"被改过"的资产
==================

缓存是某个时间点的快照。如果资产在缓存建好之后又被保存过（比如在编辑器里改了
蓝图），缓存就是**旧版本**，基于它得出的结论会过时。

时间戳比对（2026-09-27）：

* 缓存建立：18:15:04
* 之后被保存：AS_Combo01_01 (18:24:37)、AS_Combo03_02 (18:25:20)、
  AS_Combo03_04 (18:26:06)、**AC_Combat (18:26:50)**

所以这 4 个必须重采——尤其是 AC_Combat，它是连招逻辑的核心。

运行：
  <引擎 python> tools\resync_changed.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import collector  # noqa: E402

# 只列确实比缓存新的那几个；全量重采没必要（每个资产都要重开一次项目）
ASSETS = [
    "/Game/Combo_Demo/Component/AC_Combat",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo01_01",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo03_02",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo03_04",
]


def main():
    result = collector.collect(ASSETS, force=True)
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    print(text[:2500])
    if len(text) > 2500:
        print("... [截断，原长 %d]" % len(text))


if __name__ == "__main__":
    main()
