"""批量下载 sand3d 数据集的 USDA 文件（约 184.6 GiB）。

任务清单来自已下载 Parquet 的 usda_path/usda_bytes 列，天然与数据集一一对应。
64 线程、线程内复用 Session、断点续传（.part + Range）、按字节数校验、失败重试。
"""
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pyarrow.parquet as pq
import requests
from huggingface_hub import get_token

from sand3d_paths import DATA_ROOT

ROOT = DATA_ROOT
BASE = "https://huggingface.co/datasets/Sandflow/{repo}/resolve/main/{path}"
WORKERS = 64
TOKEN = os.environ.get("HF_TOKEN") or get_token()   # 登录后的 token，避免匿名限流

lock = threading.Lock()
local = threading.local()
done_bytes = 0
done_files = 0
failed = []


def session():
    """每个线程一个 Session，复用 TLS；trust_env 让请求走系统代理。"""
    if not hasattr(local, "s"):
        s = requests.Session()
        s.trust_env = True
        s.headers["User-Agent"] = "sand3d-downloader/1.0"
        if TOKEN:
            s.headers["Authorization"] = f"Bearer {TOKEN}"
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
        s.mount("https://", adapter)
        local.s = s
    return local.s


def fetch(repo, path, size):
    """下载单个文件，返回字节数；已存在且大小正确则跳过。"""
    target = os.path.join(ROOT, repo, path.replace("/", os.sep))
    if os.path.exists(target) and os.path.getsize(target) == size:
        return size, True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    for attempt in range(5):
        have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with session().get(BASE.format(repo=repo, path=path), headers=headers,
                               stream=True, timeout=(20, 60)) as resp:
                if resp.status_code == 416:      # 已有分片超出范围，重下
                    os.remove(tmp)
                    continue
                if resp.status_code == 429:      # 限流：退避后重试
                    time.sleep(30 + random.random() * 10)
                    continue
                resp.raise_for_status()
                mode = "ab" if have and resp.status_code == 206 else "wb"
                if mode == "wb":
                    have = 0
                with open(tmp, mode) as fh:
                    for block in resp.iter_content(1 << 20):
                        fh.write(block)
                        have += len(block)
            if have == size:
                os.replace(tmp, target)
                return size, False
            os.remove(tmp)                        # 大小不符，整文件重下
        except Exception:
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 20) + random.random())
    raise IOError(f"重试耗尽: {repo}/{path}")


def main():
    jobs = []
    for dirpath, _, names in os.walk(ROOT):
        for name in names:
            if not name.endswith(".parquet"):
                continue
            repo = os.path.relpath(dirpath, ROOT).split(os.sep)[0]
            table = pq.read_table(os.path.join(dirpath, name),
                                   columns=["usda_path", "usda_bytes"])
            jobs.extend((repo, r["usda_path"], r["usda_bytes"]) for r in table.to_pylist())
    total = sum(j[2] for j in jobs)
    print(f"任务 {len(jobs):,} 个，合计 {total / 2**30:.2f} GiB", flush=True)

    global done_bytes, done_files
    t0 = last = time.time()
    last_bytes = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch, *job): job for job in jobs}
        for fut in as_completed(futures):
            repo, path, size = futures[fut]
            try:
                n, skipped = fut.result()
            except Exception as exc:  # 单个失败不终止整体
                with lock:
                    failed.append((repo, path, str(exc)[:100]))
                continue
            with lock:
                done_bytes += n
                done_files += 1
            now = time.time()
            if now - last >= 30:
                speed = (done_bytes - last_bytes) / (now - last) / 2**20
                eta = (total - done_bytes) / max(done_bytes / (now - t0), 1) / 3600
                print(f"[进度] {done_files:,}/{len(jobs):,} 文件  "
                      f"{done_bytes / 2**30:.2f}/{total / 2**30:.2f} GiB  "
                      f"{speed:.1f} MiB/s  剩余约 {eta:.1f} h", flush=True)
                last, last_bytes = now, done_bytes

    used = time.time() - t0
    with open(os.path.join(os.path.dirname(ROOT), "sand3d_usda_failed.txt"),
              "w", encoding="utf-8-sig") as fh:
        for item in failed:
            fh.write("\t".join(item) + "\r\n")
    print(f"完成：{done_files:,} 个文件，{done_bytes / 2**30:.2f} GiB，"
          f"用时 {used / 3600:.2f} h；失败 {len(failed)}", flush=True)


if __name__ == "__main__":
    main()
