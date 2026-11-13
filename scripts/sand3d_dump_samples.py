"""导出每个数据集的一条完整程序，便于查看结构和入口函数。"""
import os

import pyarrow.parquet as pq

from sand3d_paths import DATA_ROOT

ROOT = DATA_ROOT
OUT = r"C:\Users\patde\AppData\Local\Temp\sand3d_samples"
WANT = {
    "sand3d-objaverse": None,
    "sand3d-cad": None,
    "sand3d-articraft": 3,
    "sand3d-automate": 2,
    "sand3d-simready": None,
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for dataset, joints in WANT.items():
        base = os.path.join(ROOT, dataset)
        picked = None
        for dirpath, _, names in os.walk(base):
            for name in sorted(names):
                if not name.endswith(".parquet"):
                    continue
                table = pq.read_table(os.path.join(dirpath, name),
                                       columns=["id", "program", "entry", "n_joints"])
                for row in table.to_pylist():
                    if joints is None or row["n_joints"] == joints:
                        picked = row
                        break
                if picked:
                    break
            if picked:
                break
        if not picked:
            print(f"{dataset}: 未找到样本")
            continue
        path = os.path.join(OUT, f"{dataset}.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(picked["program"])
        print(f"{dataset}: id={picked['id']} entry={picked['entry']} "
              f"joints={picked['n_joints']} -> {path}")


if __name__ == "__main__":
    main()
