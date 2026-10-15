"""
電力計 PM-01(店舗全体の受電)を模擬する Modbus TCP サーバー(学習用、ポート5023)

レジスタマップ(保持レジスタ / Holding Register, ファンクションコード 03)
--------------------------------------------------------------------
  0-1 : 積算電力量    32bit符号なし整数, ×0.1kWh, 下位ワードが先(リトルエンディアンのワード順)
  2-3 : 瞬時電力      32bit浮動小数点(IEEE754), kW, 上位ワードが先(ビッグエンディアンのワード順)

※ わざとワード順を混在させています。実機でも「メーカーや機種ごとに違う」のが普通なので、
   その読み解きを体験するためです。

瞬時電力は、売場空調が運転中なら約12kW上がります。空調の運転スケジュールは時計の時刻で決まっているので
(common.aircon_running)、空調のシミュレータとは別プロセスでも同じタイミングで上下します。

実行:  python simulator/power_meter.py  [--port 5023]
"""
import math
import random

from common import HR, aircon_running, f32_high_word_first, run, u32_low_word_first

state = {"energy_kwh": 123456.7}   # 積算電力量は開店からの累計。上位ワードが0にならない大きさにしておく


def step(dev, now: float, interval: float) -> None:
    power_kw = 35.0 + 12.0 * aircon_running(now) + 5.0 * math.sin(now / 600) + random.uniform(-0.8, 0.8)
    state["energy_kwh"] += power_kw * interval / 3600
    dev.setValues(HR, 0, u32_low_word_first(int(state["energy_kwh"] * 10)) + f32_high_word_first(power_kw))


if __name__ == "__main__":
    run("PM-01", "電力計", step)
