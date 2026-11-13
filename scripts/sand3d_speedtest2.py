"""测速：线程内复用 requests.Session 下载 40 个 USDA。"""
import os
import threading
import time

import pyarrow.parquet as pq
import requests

ROOT = r"c:\Users\patde\Documents\GitHub\sand3d\data\sand3d-datasets"
OUT = r"C:\Users\patde\AppData\Local\Temp\sand3d_usda_test6"
BASE = "https://hf-mirror.com/datasets/Sandflow/{repo}/resolve/main/{path}"

local = threading.local()


def session():
    """每个线程一个 Session，复用 TLS 连接。"""
    if not hasattr(local, "s"):
        local.s = requests.Session()
        local.s.trust_env = True           # 走系统代理
        local.s.headers["User-Agent"] = "sand3d-downloader/1.0"
    return local.s


def task(job):
    repo, path, size = job
    target = os.path.join(OUT, repo, path.replace("/", os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target) and os.path.getsize(target) == size:
        return 0
    tmp = target + ".part"
    with session().get(BASE.format(repo=repo, path=path), stream=True, timeout=60) as resp:
        resp.raise_for_status()
        got = 0
        with open(tmp, "wb") as fh:
            for block in resp.iter_content(1 << 20):
                fh.write(block)
                got += len(block)
    if got != size:
        os.remove(tmp)
        raise IOError(f"{path}: {got} != {size}")
    os.replace(tmp, target)
    return got


def main():
    parquet = [os.path.join(dp, f) for dp, _, fs in os.walk(ROOT)
               for f in fs if f.endswith(".parquet")]
    jobs = []
    for path in sorted(parquet):
        repo = os.path.relpath(path, ROOT).split(os.sep)[0]
        table = pq.read_table(path, columns=["usda_path", "usda_bytes"])
        jobs.extend((repo, r["usda_path"], r["usda_bytes"])
                    for r in table.to_pylist()[104:232])
        if len(jobs) >= 128:
            break
    jobs = jobs[:128]
    print(f"测试 {len(jobs)} 个文件")
    t0 = time.time()
    with __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(128) as pool:
        total = sum(pool.map(task, jobs))
    dt = time.time() - t0
    print(f"{total / 2**20:.1f} MiB / {dt:.1f}s = {total / 2**20 / dt:.1f} MiB/s")


if __name__ == "__main__":
    main()
