"""
店舗設備シミュレータの共通部品(学習用)

  機器ごとのシミュレータ(aircon.py / showcase.py / power_meter.py)は、それぞれ別のプロセス・別のポートで動く
  Modbus TCP サーバーです。実際の店舗でも、空調のコントローラ・ショーケース・電力計は別々の機器で、
  それぞれ自分のIPアドレスを持ち、1台だけ故障することがあります。

  運転のスケジュール(空調の停止、霜取り)は「起動してからの時間」ではなく「時計の時刻」で決めます。
  別プロセスでも同じ時刻なら同じ状態になるので、電力計は「今、空調が止まっているか」を自分で計算できます。
"""
import argparse
import asyncio
import struct
import time

from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import StartAsyncTcpServer

HR = 3  # 保持レジスタのファンクションコード

# 機器ごとの待ち受けポート(gateway/points.json と force_alarm.py もこの番号を使う)
PORTS = {"AC-01": 5021, "RC-01": 5022, "PM-01": 5023}


def aircon_running(now: float) -> int:
    """売場空調は10分のうち2分停止する(負荷を下げる間欠運転のつもり)"""
    return 0 if (int(now) // 120) % 5 == 4 else 1


def defrosting(now: float) -> int:
    """冷蔵ショーケースは8分のうち最後の90秒が霜取り(実機は数時間おきに20〜30分)"""
    return 1 if now % 480 >= 390 else 0


def u16(value: int) -> int:
    """符号付き整数を16bitレジスタ値(0-65535)に変換"""
    return value & 0xFFFF


def u32_low_word_first(value: int) -> list[int]:
    value &= 0xFFFFFFFF
    return [value & 0xFFFF, (value >> 16) & 0xFFFF]


def f32_high_word_first(value: float) -> list[int]:
    hi, lo = struct.unpack(">HH", struct.pack(">f", value))
    return [hi, lo]


def run(equipment_id: str, description: str, step, setup=None) -> None:
    """1台分の Modbus TCP サーバーを起動し、interval 秒ごとに step(device, now, interval) で値を更新する"""
    ap = argparse.ArgumentParser(description=f"{equipment_id} {description} の Modbus シミュレータ")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORTS[equipment_id])
    ap.add_argument("--interval", type=float, default=1.0, help="値の更新間隔(秒)")
    args = ap.parse_args()

    async def main() -> None:
        device = ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, [0] * 200))
        # single=True: デバイスID(ユニットID)を問わず、この1台として応答する
        ctx = ModbusServerContext(devices=device, single=True)
        if setup:
            setup(device)

        async def loop() -> None:
            while True:
                step(device, time.time(), args.interval)
                await asyncio.sleep(args.interval)

        print(f"{equipment_id} {description} 起動: {args.host}:{args.port}  Ctrl+Cで終了", flush=True)
        asyncio.create_task(loop())
        await StartAsyncTcpServer(context=ctx, address=(args.host, args.port))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
