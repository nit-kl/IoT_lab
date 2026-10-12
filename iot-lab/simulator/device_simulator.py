"""
空調機とPLCを模擬する Modbus TCP サーバー(学習用)

1つのTCPポートで2台分の機器を模擬します(Modbusの「デバイスID(ユニットID)」で区別)。
  デバイスID 1 : 空調機 AHU-01
  デバイスID 2 : PLC(生産ライン)

レジスタマップ(保持レジスタ / Holding Register, ファンクションコード 03)
--------------------------------------------------------------------
[空調機 AHU-01 / デバイスID 1]
  0 : 運転状態        0=停止 1=運転
  1 : 運転モード      1=冷房 2=暖房 3=送風
  2 : 設定温度        ×10 (例 260 → 26.0℃)  ※書き込み可(ハンズオン5の遠隔操作で変更)
  3 : 室温            ×10, 符号付き16bit
  4 : 湿度            ×10 (%)
  5 : 異常コード      0=正常 それ以外=異常
  10: 強制異常コード  書き込み可(テスト用)。0以外を書くと、その値が異常コードに出続ける
                      (ハンズオン4のアラーム通知の確認用。simulator/force_alarm.py で書き込む)

[PLC / デバイスID 2]
  100-101 : 生産カウンタ   32bit符号なし整数, 下位ワードが先(リトルエンディアンのワード順)
  102-103 : モータ電流     32bit浮動小数点(IEEE754), 上位ワードが先(ビッグエンディアンのワード順)
  104     : ライン稼働     0=停止 1=稼働

※ わざとワード順を混在させています。実機でも「メーカーやPLCごとに違う」のが普通なので、
   その読み解きを体験するためです。

実行:  python device_simulator.py  [--port 5020]
"""
import argparse
import asyncio
import math
import random
import struct
import time

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

HR = 3  # 保持レジスタのファンクションコード


def u16(value: int) -> int:
    """符号付き整数を16bitレジスタ値(0-65535)に変換"""
    return value & 0xFFFF


def u32_low_word_first(value: int) -> list[int]:
    value &= 0xFFFFFFFF
    return [value & 0xFFFF, (value >> 16) & 0xFFFF]


def f32_high_word_first(value: float) -> list[int]:
    hi, lo = struct.unpack(">HH", struct.pack(">f", value))
    return [hi, lo]


async def update_loop(devices: dict, interval: float) -> None:
    ahu = devices[1]
    plc = devices[2]
    start = time.time()
    counter = 0
    room_temp = 27.5
    ahu.setValues(HR, 2, [260])   # 設定温度の初期値 26.0℃(以後は外から書き換えられる)
    while True:
        t = time.time() - start

        # --- 空調機 ---
        running = 0 if (int(t) // 120) % 5 == 4 else 1   # 10分のうち2分は停止
        setpoint = ahu.getValues(HR, 2, 1)[0] / 10
        if running:
            room_temp += (setpoint - room_temp) * 0.05 + random.uniform(-0.05, 0.05)
        else:
            room_temp += (30.0 - room_temp) * 0.02 + random.uniform(-0.05, 0.05)
        humidity = 55 + 5 * math.sin(t / 300) + random.uniform(-0.5, 0.5)
        alarm = 0 if random.random() > 0.01 else 101       # 約1%の確率で異常コード101(一瞬だけ)
        forced = ahu.getValues(HR, 10, 1)[0]
        if forced:
            alarm = forced                                  # テスト用の強制異常(解除されるまで続く)
        ahu.setValues(HR, 0, [
            running,
            1,                                   # 冷房
            round(setpoint * 10),
            u16(round(room_temp * 10)),
            int(humidity * 10),
            alarm,
        ])

        # --- PLC ---
        line_on = 0 if (int(t) // 90) % 4 == 3 else 1      # 6分のうち1.5分は停止
        if line_on:
            counter += random.randint(1, 3)
        current = (12.0 + random.uniform(-0.8, 0.8)) if line_on else 0.3
        plc.setValues(HR, 100, u32_low_word_first(counter) + f32_high_word_first(current) + [line_on])

        await asyncio.sleep(interval)


async def main() -> None:
    parser = argparse.ArgumentParser(description="空調機・PLC Modbusシミュレータ")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--interval", type=float, default=1.0, help="値の更新間隔(秒)")
    args = parser.parse_args()

    devices = {
        1: ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, [0] * 200)),
        2: ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, [0] * 200)),
    }
    ctx = ModbusServerContext(devices=devices, single=False)

    print(f"シミュレータ起動: {args.host}:{args.port}  (デバイスID 1=空調機, 2=PLC)  Ctrl+Cで終了")
    asyncio.create_task(update_loop(devices, args.interval))
    await StartAsyncTcpServer(context=ctx, address=(args.host, args.port))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
