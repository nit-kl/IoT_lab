"""
店舗の設備シミュレータをまとめて起動する(学習用)

  機器ごとのシミュレータは別々のプロセス・別々のポートで動きます(1台だけ止めることもできる)。
  このスクリプトはそれらを子プロセスとして起動し、ログに機器IDを付けて1つの画面に出します。

    AC-01   売場空調          simulator/aircon.py          Modbus TCP  5021
    RC-01   冷蔵ショーケース  simulator/showcase.py        Modbus TCP  5022
    PM-01   電力計            simulator/power_meter.py     Modbus TCP  5023
    REF-01  冷凍機盤          simulator/refrigerator_plc.py  MCプロトコル 5010

実行例:
  python simulator/run_store.py                      # 全機器を起動
  python simulator/run_store.py --except RC-01       # RC-01 以外を起動(RC-01 は別のターミナルで
                                                     #   python simulator/showcase.py として起動・停止して、
                                                     #   1台だけの故障を再現する)
  python simulator/run_store.py --verbose            # 冷凍機盤が読まれたデバイスも表示
"""
import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EQUIPMENT = {
    "AC-01": "aircon.py",
    "RC-01": "showcase.py",
    "PM-01": "power_meter.py",
    "REF-01": "refrigerator_plc.py",
}


def relay(name: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        print(f"[{name:<6}] {line.rstrip()}", flush=True)
    print(f"[{name:<6}] 終了しました(終了コード {proc.wait()})", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="店舗の設備シミュレータをまとめて起動")
    ap.add_argument("--except", dest="excluded", nargs="*", default=[], choices=sorted(EQUIPMENT),
                    help="起動しない機器(別のターミナルで個別に起動・停止する)")
    ap.add_argument("--verbose", action="store_true", help="冷凍機盤が読み書きされたデバイスを表示")
    args = ap.parse_args()

    procs = []
    for name, script in EQUIPMENT.items():
        if name in args.excluded:
            print(f"[{name:<6}] 起動しません(個別に起動: python simulator/{script})")
            continue
        cmd = [sys.executable, "-u", str(HERE / script)]
        if name == "REF-01" and args.verbose:
            cmd.append("--verbose")
        # パイプ経由だとWindowsでは子プロセスの出力がcp932になるので、UTF-8にそろえる
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=relay, args=(name, proc), daemon=True).start()
        procs.append(proc)

    try:
        while any(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            p.wait()


if __name__ == "__main__":
    main()
