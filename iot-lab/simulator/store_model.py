"""
店舗の「お客様の動き」と「商品マスタ」のモデル(学習用。入店カウンタとPOSのシミュレータで共通に使う)

  入店数・退店数・取引数は、時刻(1分単位)から決まる乱数で作ります。同じ分なら何度計算しても同じ値になるので、
    - 入店カウンタのAPIは、同じ時間帯を何度問い合わせても同じ値を返す(実機のAPIと同じく、取り直しが効く)
    - POSのシミュレータとは別プロセスでも、来店客数と取引数がつじつまの合う値になる(買上率を計算できる)

  店舗は24時間営業のスーパーのつもりです(深夜は客が少なく、昼と夕方にピークがある)。
"""
import math
import random
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
STORE_CODE = "S001"

# 時間帯(日本時間)ごとの1分あたりの平均入店数
ARRIVALS_PER_MIN = [0.3] * 6 + [1.5] * 3 + [3.0] * 2 + [5.0] * 2 + [3.0] * 3 + [6.0] * 3 + [3.5] * 3 + [1.0] * 2
DWELL_MIN = 15          # 入店してから退店するまで(分)
BUY_RATE = 0.65         # 入店した人のうち、会計する人の割合(家族連れは2人で1会計になる、など)
REGISTERS = [1, 2, 3, 4]   # 4番はセルフレジ


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def arrivals(minute: int) -> int:
    """minute(UNIX時刻÷60)の1分間の入店数"""
    hour = datetime.fromtimestamp(minute * 60, JST).hour
    return _poisson(random.Random(minute * 7919), ARRIVALS_PER_MIN[hour])


def departures(minute: int) -> int:
    return arrivals(minute - DWELL_MIN)


def buyers(minute: int) -> int:
    """minute の1分間に会計する人数(DWELL_MIN 分前に入店した人の一部)"""
    rng = random.Random(minute * 104729)
    return sum(1 for _ in range(departures(minute)) if rng.random() < BUY_RATE)


# ---------------------------------------------------------------- 商品マスタ
def instore_jan(n: int) -> str:
    """店内コード(先頭20〜29。メーカーの商品コードと重ならない)のJAN-13。最後の1桁はチェックデジット"""
    body = f"20{n:010d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


_PRODUCTS = [
    # (商品名, 部門, 税込単価, 売れやすさ)
    ("牛乳 1000ml", "日配", 238, 6), ("ヨーグルト 400g", "日配", 158, 4), ("絹豆腐", "日配", 98, 4),
    ("納豆 3P", "日配", 88, 4), ("卵 10個", "日配", 248, 5), ("食パン 6枚", "パン", 168, 4),
    ("バナナ", "青果", 198, 5), ("トマト 3個", "青果", 258, 3), ("キャベツ", "青果", 178, 3),
    ("玉ねぎ 3個", "青果", 198, 3), ("豚こま切れ 300g", "精肉", 398, 3), ("鶏もも 300g", "精肉", 458, 3),
    ("刺身盛合せ", "鮮魚", 698, 2), ("幕の内弁当", "惣菜", 498, 3), ("おにぎり 鮭", "惣菜", 128, 4),
    ("唐揚げ", "惣菜", 398, 3), ("緑茶 2L", "飲料", 178, 4), ("炭酸水 500ml", "飲料", 98, 4),
    ("ビール 6缶", "飲料", 1280, 2), ("ポテトチップス", "菓子", 148, 3),
]
# 陳列場所(棚割)。日配と飲料は冷蔵ショーケース RC-01 に並ぶ(温度逸脱のときに影響を受ける商品)
LOCATIONS = {"日配": "RC-01", "飲料": "RC-01", "パン": "常温棚", "菓子": "常温棚", "青果": "青果平台",
             "精肉": "精肉鮮魚ケース", "鮮魚": "精肉鮮魚ケース", "惣菜": "惣菜コーナー"}
PRODUCTS = [{"jan": instore_jan(i + 1), "name": n, "category": c, "location": LOCATIONS[c], "price_yen": p, "weight": w}
            for i, (n, c, p, w) in enumerate(_PRODUCTS)]


def basket(rng: random.Random, hour: int) -> list[dict]:
    """1会計分の買い物かご。昼(11〜13時)は惣菜が売れやすい"""
    weights = [p["weight"] * (3 if p["category"] == "惣菜" and 11 <= hour < 13 else 1) for p in PRODUCTS]
    picks = rng.choices(PRODUCTS, weights=weights, k=rng.randint(1, 8))
    lines = {}
    for p in picks:
        lines.setdefault(p["jan"], {**p, "qty": 0})["qty"] += 1
    return list(lines.values())
