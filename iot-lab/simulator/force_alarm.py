"""
シミュレータの店舗設備に異常を起こす/解除するテスト用ツール(ハンズオン4)

  機器ごとのシミュレータ(aircon.py / showcase.py)のテスト用レジスタに Modbus で書き込みます(ファンクションコード06)。
    --code  : 強制異常コード(レジスタ10番)。0以外を書くと、その値が異常コードに出続ける。0で解除
    --door  : 冷蔵ショーケース RC-01 の強制扉開(レジスタ11番)。open で開けっ放し、close で閉める

実行例:
  python simulator/force_alarm.py --code 201              # 売場空調 AC-01 に異常コード201
  python simulator/force_alarm.py --code 0                # 解除
  python simulator/force_alarm.py --code 201 --pulse 3    # 3秒だけ発生させて自動で解除(瞬時異常)
  python simulator/force_alarm.py --door open             # ショーケースの扉を開けっ放しにする(庫内温度が上がる)
  python simulator/force_alarm.py --door close            # 扉を閉める
  python simulator/force_alarm.py --code 301 --target RC-01   # ショーケースに異常コード301
"""
import argparse
import time

from pymodbus.client import ModbusTcpClient

from common import PORTS

FORCE_CODE_REGISTER = 10
FORCE_DOOR_REGISTER = 11
TARGETS = ["AC-01", "RC-01"]   # 強制異常を書ける機器(電力計には無い)
SHOWCASE = "RC-01"


def write(host: str, target: str, register: int, value: int, label: str) -> None:
    client = ModbusTcpClient(host, port=PORTS[target], timeout=3)
    if not client.connect():
        raise SystemExit(f"{target}({host}:{PORTS[target]})に接続できません。シミュレータは起動していますか?")
    try:
        rr = client.write_register(register, value, device_id=1)
    finally:
        client.close()
    if rr.isError():
        raise SystemExit(f"書き込み失敗: {rr}")
    print(f"{target} {label}")


def main() -> None:
    ap = argparse.ArgumentParser(description="シミュレータに異常を起こす(テスト用)")
    ap.add_argument("--code", type=int, help="異常コード(0で解除)")
    ap.add_argument("--target", choices=TARGETS, default="AC-01", help="--code を書く機器")
    ap.add_argument("--door", choices=["open", "close"], help="冷蔵ショーケースの扉を開けっ放しにする/閉める")
    ap.add_argument("--pulse", type=float, default=0, help="指定秒数だけ発生させて自動で解除")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if args.code is None and args.door is None:
        ap.error("--code か --door を指定してください")

    if args.code is not None:
        write(args.host, args.target, FORCE_CODE_REGISTER, args.code,
              f"強制異常コード = {args.code}" + ("(解除)" if args.code == 0 else ""))
    if args.door is not None:
        write(args.host, SHOWCASE, FORCE_DOOR_REGISTER, int(args.door == "open"),
              "扉を開けっ放しにしました" if args.door == "open" else "扉を閉めました")
    if args.pulse:
        time.sleep(args.pulse)
        if args.code:
            write(args.host, args.target, FORCE_CODE_REGISTER, 0, "強制異常コード = 0(解除)")
        if args.door == "open":
            write(args.host, SHOWCASE, FORCE_DOOR_REGISTER, 0, "扉を閉めました")


if __name__ == "__main__":
    main()
