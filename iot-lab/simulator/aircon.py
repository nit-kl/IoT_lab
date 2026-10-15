"""
売場空調 AC-01 を模擬する Modbus TCP サーバー(学習用、ポート5021)

レジスタマップ(保持レジスタ / Holding Register, ファンクションコード 03)
--------------------------------------------------------------------
  0 : 運転状態        0=停止 1=運転(10分のうち2分は停止)
  1 : 運転モード      1=冷房 2=暖房 3=送風
  2 : 設定温度        ×10 (例 260 → 26.0℃)  ※書き込み可(ハンズオン5の遠隔操作で変更)
  3 : 室温            ×10, 符号付き16bit
  4 : 湿度            ×10 (%)
  5 : 異常コード      0=正常 それ以外=異常
  10: 強制異常コード  書き込み可(テスト用)。0以外を書くと、その値が異常コードに出続ける
                      (ハンズオン4のアラーム通知の確認用。simulator/force_alarm.py で書き込む)

実行:  python simulator/aircon.py  [--port 5021]
"""
import math
import random

from common import HR, aircon_running, run, u16

state = {"room_temp": 27.5}


def setup(dev) -> None:
    dev.setValues(HR, 2, [260])   # 設定温度の初期値 26.0℃(以後は外から書き換えられる)


def step(dev, now: float, interval: float) -> None:
    running = aircon_running(now)
    setpoint = dev.getValues(HR, 2, 1)[0] / 10
    target, rate = (setpoint, 0.05) if running else (30.0, 0.02)   # 止まると外気に近づいていく
    state["room_temp"] += (target - state["room_temp"]) * rate + random.uniform(-0.05, 0.05)
    humidity = 55 + 5 * math.sin(now / 300) + random.uniform(-0.5, 0.5)
    alarm = 0 if random.random() > 0.01 else 101       # 約1%の確率で異常コード101(一瞬だけ)
    forced = dev.getValues(HR, 10, 1)[0]
    if forced:
        alarm = forced                                  # テスト用の強制異常(解除されるまで続く)
    dev.setValues(HR, 0, [
        running,
        1,                                   # 冷房
        round(setpoint * 10),
        u16(round(state["room_temp"] * 10)),
        int(humidity * 10),
        alarm,
    ])


if __name__ == "__main__":
    run("AC-01", "売場空調", step, setup)
