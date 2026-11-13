"""把 106,094 个组件的 Parquet 元数据与程序文本写入 SQLite + FTS5 索引。

用途：检索组件时不再扫 2GB Parquet。
用法：.venv\\Scripts\\python -m tools.scene_agent.index [--datasets cad,objaverse] [--limit N]
幂等：重复运行按 id 覆盖，不产生重复行。
"""
import argparse
import glob
import hashlib
import os
import re
import sqlite3
import sys
import time

import pyarrow.parquet as pq

from . import DB_PATH, OUT

# 数据集根目录：环境变量 > H 盘云端硬盘 > 项目内 data
DRIVE_ROOT = r"H:\我的云端硬盘\sand3d-datasets"
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "data", "sand3d-datasets")
DATA_ROOT = os.environ.get("SAND3D_DATA") or (DRIVE_ROOT if os.path.isdir(DRIVE_ROOT) else LOCAL_ROOT)

COLUMNS = ["id", "source", "source_group", "family", "entry", "program", "usda_path",
           "n_joints", "f_score", "license", "attribution", "program_sha256", "sand3d_version"]

# 从程序里抽函数名与 assembly 名，作为高权重检索词
RE_DEF = re.compile(r"^\s*def\s+([A-Za-z_]\w*)", re.M)
RE_ASM = re.compile(r"""assembly\(\s*['"]([^'"]+)['"]""")


def connect():
    """打开索引库并设置 WAL，减少 IO 开销。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"), encoding="utf-8") as fh:
        con.executescript(fh.read())
    return con


def names_of(row):
    """拼 names 列：来源 / 类别 / entry / 函数名 / assembly 名。"""
    program = row["program"] or ""
    parts = [row["source"], row["source_group"], row["family"], row["entry"]]
    parts += RE_DEF.findall(program)[:40]
    parts += RE_ASM.findall(program)[:10]
    return " ".join(p.replace("_", " ") for p in parts if p)


def primary_name(row):
    """主名称：entry 优先，其次 assembly 名，最后首个函数名。"""
    if row["entry"]:
        return row["entry"]
    asms = RE_ASM.findall(row["program"] or "")
    if asms:
        return asms[0]
    funcs = RE_DEF.findall(row["program"] or "")
    return funcs[0] if funcs else row["id"]


def dataset_files(datasets=None):
    """列出要索引的 Parquet 文件。"""
    files = sorted(glob.glob(os.path.join(DATA_ROOT, "sand3d-*", "data", "**", "*.parquet"), recursive=True))
    if datasets:
        keep = {d.strip() for d in datasets.split(",") if d.strip()}
        files = [f for f in files if any(f"sand3d-{k}{os.sep}" in f for k in keep)]
    if not files:
        raise SystemExit(f"未找到 Parquet 文件，DATA_ROOT={DATA_ROOT}")
    return files


def fts_rowid(component_id):
    """由 id 派生稳定的 60 位 rowid，让 FTS 写入/覆盖都是 O(1)（按 id 删除会全表扫描）。"""
    return int(hashlib.sha1(component_id.encode("utf-8")).hexdigest()[:15], 16)


def index(files, limit=None, reset=False):
    """逐文件批量写入，返回写入行数。"""
    con = connect()
    if reset:
        with con:
            con.execute("DELETE FROM components_fts")
            con.execute("DELETE FROM components")
        print("已清空 components / components_fts")
    total, t0 = 0, time.time()
    for path in files:
        pf = pq.ParquetFile(path)
        rel = os.path.relpath(path, DATA_ROOT)
        n_file = 0
        for batch in pf.iter_batches(batch_size=2000, columns=COLUMNS):
            rows = batch.to_pylist()
            comp_rows, fts_rows = [], []
            for row in rows:
                if not row["program"] or not row["id"]:
                    continue
                comp_rows.append((
                    row["id"], row["source"], row["source_group"], row["family"], row["entry"],
                    primary_name(row), row["program"], row["program_sha256"], row["sand3d_version"],
                    row["usda_path"], row["n_joints"], row["f_score"], row["license"], row["attribution"],
                ))
                fts_rows.append((fts_rowid(row["id"]), row["id"], names_of(row), row["program"]))
            with con:  # 每个批次一个事务
                con.executemany(
                    "INSERT OR REPLACE INTO components VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", comp_rows)
                con.executemany(
                    "INSERT OR REPLACE INTO components_fts (rowid, id, names, body) VALUES (?,?,?,?)", fts_rows)
            n_file += len(comp_rows)
            if limit and total + n_file >= limit:
                break
        total += n_file
        print(f"  {rel}: {n_file} 行（累计 {total}，{time.time() - t0:.0f}s）", flush=True)
        if limit and total >= limit:
            break
    print(f"完成：{total} 行，用时 {time.time() - t0:.0f}s，库 {os.path.getsize(DB_PATH) / 1e6:.0f} MB")
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="构建组件索引")
    ap.add_argument("--datasets", help="只索引指定数据集，逗号分隔，如 cad,objaverse")
    ap.add_argument("--limit", type=int, help="最多写入行数（调试用）")
    ap.add_argument("--reset", action="store_true", help="先清空 components 与 FTS 再建（重建缓存保留）")
    args = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    files = dataset_files(args.datasets)
    print(f"DATA_ROOT={DATA_ROOT}，{len(files)} 个 Parquet 文件")
    index(files, args.limit, args.reset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
