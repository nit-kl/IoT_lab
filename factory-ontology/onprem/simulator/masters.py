"""マスタの定義。架空の 1 工場（2 エリア、4 ライン）と、システムごとに異なる設備 ID を持つ。"""

from datetime import date, timedelta

KOJO = "K01"
AREAS = {"A10": "プレス加工エリア", "A20": "組立エリア"}
LINES = {
    "L01": ("A10", "プレスライン1"),
    "L02": ("A10", "プレスライン2"),
    "L03": ("A20", "組立ライン1"),
    "L04": ("A20", "組立ライン2"),
}
MODELS = {
    "T-PR500": ("大和機工", "油圧プレス PR500", "PRESS"),
    "T-CNC30": ("東洋精機", "マシニングセンタ CNC30", "CNC"),
    "T-CV02": ("東洋精機", "搬送コンベア CV02", "CONV"),
    "T-WD10": ("関東溶接機", "溶接ロボット WD10", "WELD"),
    "T-AS20": ("大和機工", "自動組立機 AS20", "ASSY"),
    "T-IN05": ("光学検査研究所", "外観検査機 IN05", "INSP"),
    "T-MT15": ("東洋精機", "主電動機 MT15", "MOTOR"),
}
# (設備コード, ライン, 型式, 親設備, 名称, PLC 側の ID, 保全システム側の ID)
EQUIPMENT = [
    ("PRS-03", "L01", "T-PR500", None, "プレス3号機", "PRESS3", "E-0103"),
    ("PRS-03-M", "L01", "T-MT15", "PRS-03", "プレス3号機 主電動機", "PRESS3M", "E-0104"),
    ("CNC-01", "L01", "T-CNC30", None, "マシニング1号機", "CNC1", "E-0105"),
    ("CNV-01", "L01", "T-CV02", None, "コンベア1号機", "CONV1", "E-0106"),
    ("PRS-07", "L02", "T-PR500", None, "プレス7号機", "PRESS7", "E-0207"),
    ("PRS-07-M", "L02", "T-MT15", "PRS-07", "プレス7号機 主電動機", "PRESS7M", "E-0208"),
    ("CNC-02", "L02", "T-CNC30", None, "マシニング2号機", "CNC2", "E-0209"),
    ("CNV-02", "L02", "T-CV02", None, "コンベア2号機", "CONV2", "E-0210"),
    ("WLD-01", "L03", "T-WD10", None, "溶接ロボ1号機", "WELD1", "E-0301"),
    ("ASM-01", "L03", "T-AS20", None, "組立機1号機", "ASSY1", "E-0302"),
    ("INS-01", "L03", "T-IN05", None, "検査機1号機", "INSP1", "E-0303"),
    ("WLD-02", "L04", "T-WD10", None, "溶接ロボ2号機", "WELD2", "E-0401"),
    ("ASM-02", "L04", "T-AS20", None, "組立機2号機", "ASSY2", "E-0402"),
    ("INS-02", "L04", "T-IN05", None, "検査機2号機", "INSP2", "E-0403"),
]
RETIRED_EQUIPMENT = ("CNV-99", "L01", "T-CV02", None, "旧コンベア（撤去済み）", "CONV99", "E-0199")
MAIN_EQUIPMENT = {"L01": "PRS-03", "L02": "PRS-07", "L03": "ASM-01", "L04": "ASM-02"}
SENSOR_EQUIPMENT = ("PRESS3", "PRESS7", "CNC1", "CNC2", "ASSY1", "ASSY2")
PLC_ID = {e[0]: e[5] for e in EQUIPMENT}
CMMS_ID = {e[0]: e[6] for e in EQUIPMENT}

PARTS = {
    "B-SPD01": ("主軸ユニット", "ASSY", "式"),
    "B-BRG01": ("主軸軸受", "PART", "個"),
    "B-SEAL1": ("オイルシール", "PART", "個"),
    "B-BELT1": ("搬送ベルト", "PART", "本"),
    "B-NOZ01": ("溶接ノズル", "PART", "個"),
    "M-COIL1": ("鋼板コイル SPCC", "MATL", "kg"),
    "M-BOLT1": ("締結ボルト M8", "MATL", "個"),
}
BOM = [("B-SPD01", "B-BRG01", 2), ("B-SPD01", "B-SEAL1", 1)]
SUPPLIERS = {"S-101": "北斗ベアリング", "S-102": "中央鋼材", "S-103": "東日本締結", "S-104": "三協部品"}
PRODUCTS = {
    "P-100": ("ブラケットA", "L01", 12.0),
    "P-200": ("ハウジングB", "L02", 15.0),
    "P-300": ("アッシーC", "L03", 40.0),
    "P-400": ("アッシーD", "L04", 45.0),
}
EMPLOYEES = {
    "1001": ("山本 工場長", "工場長室"),
    "1002": ("青木 管理課長", "生産管理課"),
    "1003": ("石田 主任", "生産管理課"),
    "1004": ("内田 課長", "品質保証課"),
    "1101": ("江口 職長", "製造一課"),
    "1102": ("大野 職長", "製造一課"),
    "1103": ("加藤 職長", "製造二課"),
    "1104": ("木村 職長", "製造二課"),
    "1105": ("工藤 職長", "製造二課"),
    "2001": ("小林 保全", "保全課"),
    "2002": ("斎藤 保全", "保全課"),
    "2003": ("清水 保全", "保全課"),
    "2004": ("鈴木 保全", "保全課"),
    "2005": ("高橋 保全", "保全課"),
    "2006": ("田中 保全", "保全課"),
}
ROLES = {
    "R-PLANT": "工場長",
    "R-KPI": "KPI責任者",
    "R-LINE": "ライン責任者",
    "R-MNT": "保全当番",
    "R-QA": "品質保証",
    "R-PC": "生産管理",
}
MAINTENANCE_STAFF = ("2001", "2002", "2003", "2004", "2005", "2006")
CODES = {
    "ALM": {"A101": "振動上限超過", "A102": "油温上限超過", "A103": "主軸異常停止", "A201": "搬送詰まり", "A301": "溶接電流異常"},
    "TEISHI": {"T10": "故障", "T20": "段取り替え", "T30": "チョコ停"},
    "FURYO": {"F10": "寸法不良", "F20": "割れ", "F30": "外観不良"},
    "JOTAI": {"PLAN": "計画", "RUN": "実行中", "DONE": "完了"},
    "KPI": {"OEE": "設備総合効率", "YIELD": "歩留り", "KWH_U": "電力原単位"},
    "METRIC": {"VIB": "振動", "TEMP": "油温"},
}
KPI_TARGETS = {"OEE": 0.85, "YIELD": 0.98, "KWH_U": 0.12}


def build_masters(end):
    """全マスタの行を表名ごとに返す。担当の交代は end の 5 日前（期間つきの関係の例）。"""
    today = end.date()
    ts = end - timedelta(days=120)
    def row(**kw):
        return {**kw, "UPD_TS": ts, "DEL_FLG": "0"}
    equipment = [*EQUIPMENT, RETIRED_EQUIPMENT]
    kikai_rows = [
        row(KIKAI_CD=e[0], LINE_CD=e[1], KATA_CD=e[2], OYA_KIKAI_CD=e[3], KIKAI_NM=e[4], SECCHI_DT=date(2018, 4, 1))
        for e in equipment
    ]
    kikai_rows[-1]["DEL_FLG"] = "1"
    # 親が先に入るよう、親のない設備を先に並べる（自己参照の外部キーのため）
    kikai_rows.sort(key=lambda r: r["OYA_KIKAI_CD"] is not None)
    alias = []
    for e in equipment:
        alias.append(row(SYS_KBN="PLC", GAIBU_CD=e[5], KIKAI_CD=e[0]))
        alias.append(row(SYS_KBN="CMMS", GAIBU_CD=e[6], KIKAI_CD=e[0]))
    for a in alias[-2:]:  # 撤去した設備の別名も論理削除
        a["DEL_FLG"] = "1"

    assignments = [
        ("1001", "R-PLANT", "KOJO", KOJO, date(2024, 4, 1), None),
        ("1002", "R-KPI", "KOJO", KOJO, date(2024, 4, 1), None),
        ("1003", "R-PC", "KOJO", KOJO, date(2024, 4, 1), None),
        ("1004", "R-QA", "KOJO", KOJO, date(2024, 4, 1), None),
        ("1101", "R-LINE", "LINE", "L01", date(2024, 4, 1), None),
        ("1102", "R-LINE", "LINE", "L02", date(2024, 4, 1), None),
        ("1103", "R-LINE", "LINE", "L03", date(2024, 4, 1), today - timedelta(days=6)),
        ("1105", "R-LINE", "LINE", "L03", today - timedelta(days=5), None),
        ("1104", "R-LINE", "LINE", "L04", date(2024, 4, 1), None),
    ]
    assignments += [(s, "R-MNT", "AREA", a, date(2024, 4, 1), None) for s, a in zip(MAINTENANCE_STAFF, ("A10", "A10", "A10", "A20", "A20", "A20"))]

    return {
        "M_KOJO": [row(KOJO_CD=KOJO, KOJO_NM="第一工場")],
        "M_AREA": [row(AREA_CD=c, KOJO_CD=KOJO, AREA_NM=n) for c, n in AREAS.items()],
        "M_LINE": [row(LINE_CD=c, AREA_CD=a, LINE_NM=n) for c, (a, n) in LINES.items()],
        "M_KATASHIKI": [row(KATA_CD=c, MAKER_NM=m, KATA_NM=n, KIKI_KBN=k) for c, (m, n, k) in MODELS.items()],
        "M_KIKAI": kikai_rows,
        "M_KIKAI_ALIAS": alias,
        "M_BUHIN": [row(BUHIN_CD=c, BUHIN_NM=n, BUHIN_KBN=k, TANI=t) for c, (n, k, t) in PARTS.items()],
        "M_BUHIN_KOSEI": [row(OYA_BUHIN_CD=p, KO_BUHIN_CD=c, SURYO=q) for p, c, q in BOM],
        "M_SHIIRESAKI": [row(SHIIRE_CD=c, SHIIRE_NM=n) for c, n in SUPPLIERS.items()],
        "M_HINMOKU": [row(HIN_CD=c, HIN_NM=n, HYOJUN_CT_SEC=ct) for c, (n, _, ct) in PRODUCTS.items()],
        "M_SHAIN": [row(SHAIN_NO=c, SHAIN_NM=n, SHOZOKU_NM=d) for c, (n, d) in EMPLOYEES.items()],
        "M_YAKUWARI": [row(YAKU_CD=c, YAKU_NM=n) for c, n in ROLES.items()],
        "M_TANTO_HANI": [
            row(TANTO_ID=i, SHAIN_NO=s, YAKU_CD=r, HANI_KBN=k, HANI_CD=c, KAISHI_DT=f, SHURYO_DT=t)
            for i, (s, r, k, c, f, t) in enumerate(assignments, 1)
        ],
        "M_CODE": [row(CD_KBN=k, CD=c, CD_NM=n) for k, codes in CODES.items() for c, n in codes.items()],
        "M_KPI_MOKUHYO": [row(LINE_CD=l, KPI_CD=k, MOKUHYO_VAL=v) for l in LINES for k, v in KPI_TARGETS.items()],
    }
