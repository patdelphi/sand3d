"""用 xet 批量通道补齐本地缺失的 USDA（当前只下 cad，objaverse 交给 Colab）。"""
import time

from huggingface_hub import snapshot_download

from sand3d_paths import DATA_ROOT

ROOT = DATA_ROOT
REPOS = ["sand3d-cad"]          # 本地只补 cad；objaverse 不再本地下载

for repo in REPOS:
    t0 = time.time()
    print(f"=== {repo} 开始 ===", flush=True)
    snapshot_download(
        repo_id=f"Sandflow/{repo}",
        repo_type="dataset",
        local_dir=ROOT + "\\" + repo,
        allow_patterns=["usda/**"],
        max_workers=128,
    )
    print(f"=== {repo} 完成，用时 {(time.time() - t0) / 60:.1f} 分钟 ===", flush=True)
