"""
シミュレータの空調機(AHU-01)に異常を起こす/解除するテスト用ツール(ハンズオン4)

  保持レジスタ10番(強制異常コード)に Modbus で書き込みます(ファンクションコード06)。
  0以外を書くと、その値が異常コード(レジスタ5番)に出続けます。0を書くと解除です。

実行例:
  python simulator/force_alarm.py --code 201     # 異常コード201を発生させる
  python simulator/force_alarm.py --code 0       # 解除
  python simulator/force_alarm.py --code 201 --pulse 3   # 3秒だけ発生させて自動で解除(瞬時異常)
"""
import argparse
import time

from pymodbus.client import ModbusTcpClient

FORCE_REGISTER = 10
DEVICE_ID = 1   # 空調機 AHU-01


def write(client: ModbusTcpClient, code: int) -> None:
    rr = client.write_register(FORCE_REGISTER, code, device_id=DEVICE_ID)
    if rr.isError():
        raise SystemExit(f"書き込み失敗: {rr}")
    print(f"強制異常コード = {code}" + ("(解除)" if code == 0 else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="シミュレータに異常を起こす(テスト用)")
    ap.add_argument("--code", type=int, required=True, help="異常コード(0で解除)")
    ap.add_argument("--pulse", type=float, default=0, help="指定秒数だけ発生させて自動で解除")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    args = ap.parse_args()

    client = ModbusTcpClient(args.host, port=args.port, timeout=3)
    if not client.connect():
        raise SystemExit(f"{args.host}:{args.port} に接続できません。device_simulator.py は起動していますか?")
    try:
        write(client, args.code)
        if args.pulse and args.code:
            time.sleep(args.pulse)
            write(client, 0)
    finally:
        client.close()


if __name__ == "__main__":
    main()
