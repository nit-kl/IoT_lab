"""Oracle の表を読み、ナレッジグラフ(build/current.nq)を作る。

    python tools/build_graph.py                  # 読み取り専用ユーザーで Oracle から読む
    python tools/build_graph.py --from-generator # Oracle なしで模擬データの生成結果から作る
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyoxigraph import RdfFormat, serialize

from kg.common import BUILD_DIR, CURRENT, ONPREM, REPORT
from kg.transform import convert_tables, load_mapping


def read_oracle(mapping):
    import oracledb
    from dotenv import load_dotenv

    load_dotenv(ONPREM / ".env")
    owner = os.environ["FACTORY_APP_USER"]
    tables = {}
    with oracledb.connect(user=os.environ["FACTORY_RO_USER"], password=os.environ["FACTORY_RO_PASSWORD"],
                          dsn=os.environ["ORACLE_DSN"]) as conn, conn.cursor() as cur:
        for table in mapping:
            cur.execute(f"SELECT * FROM {owner}.{table['name']}")  # 表名は mapping.toml の固定値
            names = [d.name for d in cur.description]
            tables[table["name"]] = [dict(zip(names, row)) for row in cur]
    return tables


def read_generator():
    sys.path.insert(0, str(ONPREM / "simulator"))
    from generate import generate
    return generate()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-generator", action="store_true")
    args = parser.parse_args()

    mapping = load_mapping()
    started = time.perf_counter()
    tables = read_generator() if args.from_generator else read_oracle(mapping)
    read_seconds = time.perf_counter() - started
    result = convert_tables(tables, mapping)
    BUILD_DIR.mkdir(exist_ok=True)
    serialize(result.quads, output=str(CURRENT), format=RdfFormat.N_QUADS)
    report = {"quads": len(result.quads), "counts": result.counts, "issues": result.issues}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'表':20} {'元の行':>7} {'削除済':>6} {'変換':>6} {'隔離':>4}")
    for name, c in result.counts.items():
        note = "  グラフ対象外" if "not_in_graph" in c else ""
        print(f"{name:20} {c['source']:>7} {c['deleted']:>6} {c['converted']:>6} {c['quarantined']:>4}{note}")
    print(f"クワッド {len(result.quads)} 件 / データの問題 {len(result.issues)} 件")
    print(f"読み取り {read_seconds:.1f} 秒 / 全体 {time.perf_counter() - started:.1f} 秒 / {CURRENT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
