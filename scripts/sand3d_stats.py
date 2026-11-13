"""对已下载的 Parquet 做列级统计：关节数、版本、许可、程序完整性。"""
import os
from collections import Counter

import pyarrow.parquet as pq

ROOT = r"c:\Users\patde\Documents\GitHub\sand3d\data\sand3d-datasets"


def main():
    totals = Counter()
    joints = 0
    articulated = 0
    versions = Counter()
    licenses = Counter()
    status = Counter()
    bad_program = 0
    empty_render = 0

    for dataset in sorted(os.listdir(ROOT)):
        base = os.path.join(ROOT, dataset)
        if not os.path.isdir(base):
            continue
        for dirpath, _, names in os.walk(base):
            for name in sorted(names):
                if not name.endswith(".parquet"):
                    continue
                table = pq.read_table(
                    os.path.join(dirpath, name),
                    columns=["program", "n_joints", "render", "sand3d_version",
                             "license", "license_status"],
                )
                totals[dataset] += table.num_rows
                col = table.column("n_joints").to_pylist()
                joints += sum(col)
                articulated += sum(1 for v in col if v and v > 0)
                versions.update(table.column("sand3d_version").to_pylist())
                licenses.update(table.column("license").to_pylist())
                status.update(table.column("license_status").to_pylist())
                bad_program += sum(1 for p in table.column("program").to_pylist()
                                   if not p or "import sand3d" not in p)
                empty_render += sum(1 for r in table.column("render").to_pylist()
                                    if not r or not r.get("bytes"))

    print(f"总物体: {sum(totals.values()):,}")
    print(f"带关节: {articulated:,}   关节总数: {joints:,}")
    print(f"程序缺失/未 import sand3d: {bad_program}")
    print(f"渲染图为空: {empty_render}")
    print(f"许可: {dict(licenses)}")
    print(f"许可状态: {dict(status)}")
    print(f"sand3d 版本分布: {dict(versions)}")


if __name__ == "__main__":
    main()
