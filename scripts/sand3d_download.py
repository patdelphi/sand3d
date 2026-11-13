"""下载 Sandflow 的 5 个 sand3d 数据集（仅 Parquet）。

用标准库 urllib 流式下载，逐文件校验字节数；支持断点跳过与重试。
输出目录：<项目>/data/sand3d-datasets/<数据集>/<原路径>
"""
import os
import sys
import time
import urllib.request

DEST_ROOT = r"c:\Users\patde\Documents\GitHub\sand3d\data\sand3d-datasets"
BASE = "https://huggingface.co/datasets/Sandflow/{repo}/resolve/main/{path}"

# (数据集, 相对路径, 预期字节数)
FILES = [
    ("sand3d-objaverse", "data/objaverse/train-00000-of-00013.parquet", 119353516),
    ("sand3d-objaverse", "data/objaverse/train-00001-of-00013.parquet", 120238739),
    ("sand3d-objaverse", "data/objaverse/train-00002-of-00013.parquet", 119794857),
    ("sand3d-objaverse", "data/objaverse/train-00003-of-00013.parquet", 120400273),
    ("sand3d-objaverse", "data/objaverse/train-00004-of-00013.parquet", 120945988),
    ("sand3d-objaverse", "data/objaverse/train-00005-of-00013.parquet", 121341592),
    ("sand3d-objaverse", "data/objaverse/train-00006-of-00013.parquet", 120075496),
    ("sand3d-objaverse", "data/objaverse/train-00007-of-00013.parquet", 120664263),
    ("sand3d-objaverse", "data/objaverse/train-00008-of-00013.parquet", 121683200),
    ("sand3d-objaverse", "data/objaverse/train-00009-of-00013.parquet", 121869564),
    ("sand3d-objaverse", "data/objaverse/train-00010-of-00013.parquet", 121175357),
    ("sand3d-objaverse", "data/objaverse/train-00011-of-00013.parquet", 120338534),
    ("sand3d-objaverse", "data/objaverse/train-00012-of-00013.parquet", 114298366),
    ("sand3d-objaverse", "data/objaverse/validation-00000-of-00001.parquet", 50770684),
    ("sand3d-cad", "data/cad/train-00000-of-00005.parquet", 46985626),
    ("sand3d-cad", "data/cad/train-00001-of-00005.parquet", 46837452),
    ("sand3d-cad", "data/cad/train-00002-of-00005.parquet", 46659228),
    ("sand3d-cad", "data/cad/train-00003-of-00005.parquet", 34495151),
    ("sand3d-cad", "data/cad/train-00004-of-00005.parquet", 21835646),
    ("sand3d-cad", "data/cad/validation-00000-of-00001.parquet", 9379049),
    ("sand3d-articraft", "data/articraft/train-00000-of-00002.parquet", 75163313),
    ("sand3d-articraft", "data/articraft/train-00001-of-00002.parquet", 56663053),
    ("sand3d-articraft", "data/articraft/validation-00000-of-00001.parquet", 5066394),
    ("sand3d-automate", "data/automate-parts/train-00000-of-00001.parquet", 21756597),
    ("sand3d-automate", "data/automate-parts/validation-00000-of-00001.parquet", 2927787),
    ("sand3d-automate", "data/automate/train-00000-of-00001.parquet", 10289998),
    ("sand3d-automate", "data/automate/validation-00000-of-00001.parquet", 1161867),
    ("sand3d-simready", "data/simready/train-00000-of-00001.parquet", 54171747),
    ("sand3d-simready", "data/simready/validation-00000-of-00001.parquet", 2880904),
]

CHUNK = 1024 * 1024


def human(n):
    return f"{n / 1048576:.1f} MiB"


def download(url, target, expected, attempt=1):
    """流式下载单个文件；失败重试 3 次，成功后校验字节数。"""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    t0 = time.time()
    got = 0
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:
        while True:
            block = resp.read(CHUNK)
            if not block:
                break
            fh.write(block)
            got += len(block)
            if got // CHUNK % 200 == 0:  # 每约 200MB 报一次进度
                print(f"    {human(got)} / {human(expected)}", flush=True)
    if got != expected:
        os.remove(tmp)
        raise IOError(f"字节数不符: 得到 {got}, 预期 {expected}")
    os.replace(tmp, target)
    print(f"    完成 {human(got)}，用时 {time.time() - t0:.1f}s", flush=True)


def main():
    total_expected = sum(f[2] for f in FILES)
    print(f"目标目录: {DEST_ROOT}", flush=True)
    print(f"共 {len(FILES)} 个文件，预计 {human(total_expected)}", flush=True)
    failed = []
    for i, (repo, path, size) in enumerate(FILES, 1):
        target = os.path.join(DEST_ROOT, repo, path.replace("/", os.sep))
        tag = f"[{i}/{len(FILES)}] {repo}/{os.path.basename(path)}"
        if os.path.exists(target) and os.path.getsize(target) == size:
            print(f"{tag} 已存在，跳过", flush=True)
            continue
        url = BASE.format(repo=repo, path=path)
        print(f"{tag} {human(size)}", flush=True)
        for attempt in range(1, 4):
            try:
                download(url, target, size)
                break
            except Exception as exc:  # 网络或校验失败，退避后重试
                print(f"    第 {attempt} 次失败: {exc}", flush=True)
                if attempt == 3:
                    failed.append((repo, path, str(exc)))
                else:
                    time.sleep(3 * attempt)
    actual = sum(os.path.getsize(os.path.join(dp, f))
                 for dp, _, fs in os.walk(DEST_ROOT) for f in fs)
    print(f"\n下载结束：实际 {human(actual)}", flush=True)
    if failed:
        print("失败文件:", flush=True)
        for item in failed:
            print("  ", item, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
