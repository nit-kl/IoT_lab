"""
冷蔵ショーケース RC-01 を模擬する Modbus TCP サーバー(学習用、ポート5022)

  扉付きのリーチイン型(飲料・日配品)のつもりです。

レジスタマップ(保持レジスタ / Holding Register, ファンクションコード 03)
--------------------------------------------------------------------
  0 : 運転状態        0=停止 1=運転
  1 : 設定温度        ×10, 符号付き16bit (例 50 → 5.0℃)
  2 : 庫内温度        ×10, 符号付き16bit
  3 : 霜取り中        0=通常 1=霜取り中(8分ごとに90秒。霜取り中は庫内温度が上がる)
  4 : 扉開            0=閉 1=開
  5 : 異常コード      0=正常 それ以外=異常
  10: 強制異常コード  書き込み可(テスト用)
  11: 強制扉開        書き込み可(テスト用)。1を書くと扉が開けっ放しになり、庫内温度が上がっていく
  ※ 実機の霜取りは数時間おきに20〜30分ほど。学習用に短く・頻繁にしています

実行:  python simulator/showcase.py  [--port 5022]
"""
import random

from common import HR, defrosting, run, u16

SETPOINT = 5.0
state = {"case_temp": SETPOINT}


def step(dev, now: float, interval: float) -> None:
    defrost = defrosting(now)
    door = dev.getValues(HR, 11, 1)[0]                  # テスト用の強制扉開
    if defrost:
        target, rate = 13.0, 0.04                       # 冷却を止めて霜を溶かすので温度が上がる
    elif door:
        target, rate = 16.0, 0.02                       # 冷気が逃げて、売場の温度に近づいていく
    else:
        target, rate = SETPOINT, 0.1                    # 霜取りが終わると速やかに冷える
    state["case_temp"] += (target - state["case_temp"]) * rate + random.uniform(-0.05, 0.05)
    dev.setValues(HR, 0, [
        1,
        u16(round(SETPOINT * 10)),
        u16(round(state["case_temp"] * 10)),
        defrost,
        door,
        dev.getValues(HR, 10, 1)[0],                    # 異常コード(強制異常コードをそのまま出す)
    ])


if __name__ == "__main__":
    run("RC-01", "冷蔵ショーケース", step)
