"""场景 Agent：自然语言布置房间。

模块划分：
  index     Parquet 数据集 -> SQLite + FTS5 索引
  search    中文关键词 -> 组件候选
  rebuild   执行数据集里的 sand3d 程序 -> GLB 缓存 + 包围盒
  normalize 单位 / 落地 / 居中归一化
  builder   布局 JSON -> Blender 场景
  render    Cycles CPU 无头渲染
  llm       DashScope(OpenAI 兼容) 文本与视觉调用
  agent     布局生成与视觉迭代主循环
  cli       一条命令跑完整个流程
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "out", "scene")
DB_PATH = os.path.join(OUT, "index.db")
CACHE_DIR = os.path.join(OUT, "cache", "components")
SCENE_DIR = os.path.join(OUT, "scenes")
SRC_DIR = os.path.join(ROOT, "src")
