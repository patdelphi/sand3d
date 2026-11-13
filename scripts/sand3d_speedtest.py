"""测速：16 线程直链下载 40 个 USDA 到临时目录。"""
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq

from sand3d_paths import DATA_ROOT

ROOT = DATA_ROOT
OUT = r"C:\Users\patde\AppData\Local\Temp\sand3d_usda_test"
BASE = "https://huggingface.co/datasets/Sandflow/{repo}/resolve/main/{path}"


def task(job):
    repo, path, size = job
    target = os.path.join(OUT, repo, path.replace("/", os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target) and os.path.getsize(target) == size:
        return 0
    tmp = target + ".part"
    req = urllib.request.Request(BASE.format(repo=repo, path=path),
                                 headers={"User-Agent": "sand3d-downloader/1.0"})
    got = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
            got += len(block)
    if got != size:
        os.remove(tmp)
        raise IOError(f"{path}: {got} != {size}")
    os.replace(tmp, target)
    return got


def main():
    jobs = []
    parquet = [os.path.join(dp, f) for dp, _, fs in os.walk(ROOT)
               for f in fs if f.endswith(".parquet")]
    for path in sorted(parquet):
        repo = os.path.relpath(path, ROOT).split(os.sep)[0]
        table = pq.read_table(path, columns=["usda_path", "usda_bytes"])
        jobs.extend((repo, r["usda_path"], r["usda_bytes"])
                    for r in table.to_pylist()[:40])
        if len(jobs) >= 40:
            break
    jobs = jobs[:40]
    print(f"测试 {len(jobs)} 个文件")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=16) as pool:
        total = sum(pool.map(task, jobs))
    dt = time.time() - t0
    print(f"{total / 2**20:.1f} MiB / {dt:.1f}s = {total / 2**20 / dt:.1f} MiB/s")


if __name__ == "__main__":
    main()
