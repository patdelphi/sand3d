"""CLIP 图像向量：给数据集内嵌缩略图建向量，支持语义检索与关键词重排。

模型：open_clip ViT-B-32（laion2b_s34b_b79k），有 CUDA 时用 GPU。
存储：SQLite 表 clip_vectors(id, dim, vec)，float16 小端字节，已 L2 归一化。

用法：
  .venv\\Scripts\\python -m tools.scene_agent.clipidx build             # 全库建向量
  .venv\\Scripts\\python -m tools.scene_agent.clipidx build --limit 5000 --datasets cad
  .venv\\Scripts\\python -m tools.scene_agent.clipidx query "a red modern sofa"
"""
import argparse
import glob
import io
import os
import sqlite3
import sys
import time

import numpy as np
import pyarrow.parquet as pq

from . import DB_PATH

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
BATCH = 64

DRIVE_ROOT = r"H:\我的云端硬盘\sand3d-datasets"
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "data", "sand3d-datasets")
DATA_ROOT = os.environ.get("SAND3D_DATA") or (DRIVE_ROOT if os.path.isdir(DRIVE_ROOT) else LOCAL_ROOT)

_CACHE = {}   # 进程内缓存：模型 / 向量矩阵


def device():
    """优先用 CUDA，否则回退 CPU。"""
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_model():
    """加载 CLIP 模型、图像预处理与分词器（同进程复用）。"""
    if "model" in _CACHE:
        return _CACHE["model"], _CACHE["preprocess"], _CACHE["tokenizer"]
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=device())
    model.eval()
    _CACHE.update(model=model, preprocess=preprocess, tokenizer=open_clip.get_tokenizer(MODEL_NAME))
    return model, preprocess, _CACHE["tokenizer"]


def connect():
    """打开索引库并确保向量表存在。"""
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
    con.execute("CREATE TABLE IF NOT EXISTS clip_vectors ("
                " id TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS clip_meta (key TEXT PRIMARY KEY, value TEXT)")
    return con


def parquet_files(datasets=None):
    """列出要处理的 Parquet 文件。"""
    files = sorted(glob.glob(os.path.join(DATA_ROOT, "sand3d-*", "data", "**", "*.parquet"), recursive=True))
    if datasets:
        keep = {d.strip() for d in datasets.split(",") if d.strip()}
        files = [f for f in files if any(f"sand3d-{k}{os.sep}" in f for k in keep)]
    return files


def _encode_images(model, preprocess, blobs):
    """一批 PNG 字节 -> 归一化向量（float16 numpy）。"""
    import torch
    from PIL import Image
    tensors = []
    kept = []
    for cid, blob in blobs:
        try:
            img = Image.open(io.BytesIO(blob)).convert("RGB")
        except Exception:
            continue
        tensors.append(preprocess(img))
        kept.append(cid)
    if not tensors:
        return kept, None
    batch = torch.stack(tensors).to(device())
    with torch.no_grad():
        feats = model.encode_image(batch)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return kept, feats.detach().cpu().numpy().astype(np.float16)


def build(datasets=None, limit=None, skip_existing=True):
    """遍历 Parquet 的 render 图片建向量，幂等覆盖。"""
    model, preprocess, _ = load_model()
    con = connect()
    done = set()
    if skip_existing:
        done = {r[0] for r in con.execute("SELECT id FROM clip_vectors")}
        print(f"已有向量 {len(done)} 条，跳过")
    files = parquet_files(datasets)
    print(f"DATA_ROOT={DATA_ROOT}，{len(files)} 个文件，设备 {device()}")
    total, t0 = 0, time.time()
    for path in files:
        rel = os.path.relpath(path, DATA_ROOT)
        for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH, columns=["id", "render"]):
            rows = batch.to_pylist()
            blobs = [(r["id"], (r["render"] or {}).get("bytes"))
                     for r in rows if r["id"] not in done and (r["render"] or {}).get("bytes")]
            if not blobs:
                continue
            kept, feats = _encode_images(model, preprocess, blobs)
            if feats is None:
                continue
            with con:
                con.executemany("INSERT OR REPLACE INTO clip_vectors (id, dim, vec) VALUES (?,?,?)",
                                [(cid, feats.shape[1], feats[i].tobytes()) for i, cid in enumerate(kept)])
            total += len(kept)
            if total % 5000 < BATCH:
                print(f"  {rel}: 已编码 {total}，{time.time() - t0:.0f}s", flush=True)
        if limit and total >= limit:
            break
        print(f"  {rel} 完成，累计 {total}", flush=True)
    with con:
        con.execute("INSERT OR REPLACE INTO clip_meta VALUES ('model', ?)", (f"{MODEL_NAME}/{PRETRAINED}",))
    print(f"完成：{total} 条，用时 {time.time() - t0:.0f}s")


def encode_text(query):
    """文本 -> 归一化向量（float32 numpy）。"""
    import torch
    model, _, tokenizer = load_model()
    tokens = tokenizer([query]).to(device())
    with torch.no_grad():
        feat = model.encode_text(tokens)
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat.detach().cpu().numpy().astype(np.float32)[0]


def matrix(con=None):
    """把全库向量读成矩阵并缓存（float32，约 220MB）。"""
    if "matrix" in _CACHE:
        return _CACHE["ids"], _CACHE["matrix"]
    own = con is None
    con = con or connect()
    try:
        rows = con.execute("SELECT id, dim, vec FROM clip_vectors").fetchall()
    finally:
        if own:
            con.close()
    if not rows:
        raise RuntimeError("向量库为空：先运行 python -m tools.scene_agent.clipidx build")
    dim = rows[0][1]
    ids = [r[0] for r in rows]
    mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float16).reshape(len(rows), dim)
    _CACHE.update(ids=ids, matrix=mat.astype(np.float32))
    return _CACHE["ids"], _CACHE["matrix"]


def semantic_search(query, limit=10):
    """纯语义检索：返回 [(id, score)]。"""
    ids, mat = matrix()
    scores = mat @ encode_text(query)
    idx = np.argsort(-scores)[:limit]
    return [(ids[i], float(scores[i])) for i in idx]


def vectors_for(candidate_ids, con=None):
    """取指定 id 的向量，返回 (ids, matrix)；缺失的 id 被忽略。"""
    own = con is None
    con = con or connect()
    try:
        marks = ",".join("?" * len(candidate_ids))
        rows = con.execute(f"SELECT id, dim, vec FROM clip_vectors WHERE id IN ({marks})",
                           list(candidate_ids)).fetchall()
    finally:
        if own:
            con.close()
    if not rows:
        return [], None
    dim = rows[0][1]
    mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float16).reshape(len(rows), dim)
    return [r[0] for r in rows], mat.astype(np.float32)


def rerank(query, candidates, limit=None):
    """用 CLIP 给关键词候选重排；没有向量的候选保留在末尾（按原顺序）。"""
    if not candidates:
        return candidates
    ids, mat = vectors_for([c["id"] for c in candidates])
    if not ids:
        return candidates[:limit] if limit else candidates
    scores = mat @ encode_text(query)
    by_score = {cid: float(s) for cid, s in zip(ids, scores)}
    ranked = sorted(candidates, key=lambda c: -by_score.get(c["id"], -1.0))
    for item in ranked:
        if item["id"] in by_score:
            item["clip_score"] = round(by_score[item["id"]], 4)
    return ranked[:limit] if limit else ranked


def main(argv=None):
    ap = argparse.ArgumentParser(description="CLIP 图像向量")
    ap.add_argument("action", choices=["build", "query", "stats"])
    ap.add_argument("text", nargs="*", help="query 时输入文本")
    ap.add_argument("--datasets", help="只处理指定数据集，逗号分隔")
    ap.add_argument("--limit", type=int, help="最多编码多少张（调试）")
    ap.add_argument("--top", type=int, default=10, help="query 返回条数")
    args = ap.parse_args(argv)

    if args.action == "build":
        build(args.datasets, args.limit)
    elif args.action == "query":
        text = " ".join(args.text)
        if not text:
            ap.error("query 需要文本")
        con = connect()
        for cid, score in semantic_search(text, args.top):
            row = con.execute("SELECT name, family, source FROM components WHERE id=?", (cid,)).fetchone()
            print(f"{score:6.3f}  {cid[:46]:46s} {row[0] if row else '?':<26s} {row[1] if row else '?'}")
    else:
        con = connect()
        n = con.execute("SELECT COUNT(*) FROM clip_vectors").fetchone()[0]
        meta = con.execute("SELECT value FROM clip_meta WHERE key='model'").fetchone()
        print(f"向量 {n} 条，模型 {meta[0] if meta else '未知'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
