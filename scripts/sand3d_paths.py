"""定位 sand3d 数据集根目录。

优先级：
  1. 环境变量 SAND3D_DATA
  2. H 盘 Google Drive 挂载目录（H:\\我的云端硬盘\\sand3d-datasets）
  3. 项目内 data/sand3d-datasets

所有数据脚本统一从这里取路径，避免各自硬编码。
"""
import os

DRIVE_ROOT = r"H:\我的云端硬盘\sand3d-datasets"
LOCAL_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "sand3d-datasets",
)

DATA_ROOT = os.environ.get("SAND3D_DATA") or (
    DRIVE_ROOT if os.path.isdir(DRIVE_ROOT) else LOCAL_ROOT
)
