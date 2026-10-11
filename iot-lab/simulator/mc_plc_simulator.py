"""
三菱電機 MELSEC iQ-R を模擬する MCプロトコル(SLMP)サーバー(学習用)

  通信形式 : 3Eフレーム / バイナリコード / TCP
  対応コマンド : 一括読出 0401(ワード単位・ビット単位)
                 一括書込 1401(ワード単位のみ。学習用に書き込みの体験ができるように)
  デバイス : D(データレジスタ), M(内部リレー), W(リンクレジスタ, 16進番号)
  サブコマンド 0000/0001(Q/L形式) と 0002/0003(iQ-R形式)の両方に応答します

デバイス割り付け(PLCプログラマから受け取る「デバイス表」のつもりで見てください)
--------------------------------------------------------------------
  D100-D101 : 生産カウンタ     32bit整数(BIN32)。D100が下位ワード、D101が上位ワード
  D102      : 搬送速度         ×10 (例 125 → 12.5 m/min)
  D104-D105 : モータ電流       単精度実数(FLOAT32)。D104が下位ワード
  D110      : 異常コード       0=正常
  M0        : ライン稼働中     1=稼働
  M1        : 異常発生中       1=異常

実行:  python mc_plc_simulator.py [--port 5010]
"""
import argparse
import asyncio
import random
import struct
import time

# デバイスコード(バイナリ) → (デバイス名, 種類)
DEVICE_CODES = {0xA8: ("D", "word"), 0xB4: ("W", "word"), 0x90: ("M", "bit")}

END_OK = 0x0000
ERR_COMMAND = 0xC059      # コマンド・サブコマンド指定誤り
ERR_DEVICE = 0xC056       # デバイス範囲外
ERR_REQUEST = 0xC061      # 要求データ長が合わない


class PlcMemory:
    def __init__(self) -> None:
        self.D = [0] * 12288
        self.W = [0] * 8192
        self.M = [0] * 8192


def f32_to_words_low_first(value: float) -> list[int]:
    lo, hi = struct.unpack("<HH", struct.pack("<f", value))
    return [lo, hi]


async def run_line(mem: PlcMemory, interval: float) -> None:
    """ラインの動きを模擬して、デバイスの値を更新し続ける"""
    start = time.time()
    count = 0
    while True:
        t = time.time() - start
        running = 0 if (int(t) // 100) % 4 == 3 else 1          # 400秒のうち100秒は停止
        alarm_code = 0 if random.random() > 0.01 else 3021      # 時々、異常コード3021
        if running:
            count += random.randint(1, 3)
        speed = (12.5 + random.uniform(-0.3, 0.3)) if running else 0.0
        current = (8.0 + random.uniform(-0.5, 0.5)) if running else 0.2

        mem.D[100] = count & 0xFFFF
        mem.D[101] = (count >> 16) & 0xFFFF
        mem.D[102] = int(round(speed * 10))
        mem.D[104:106] = f32_to_words_low_first(current)
        mem.D[110] = alarm_code
        mem.M[0] = running
        mem.M[1] = 1 if alarm_code else 0
        await asyncio.sleep(interval)


def build_response(header: bytes, end_code: int, data: bytes = b"") -> bytes:
    """3Eフレームの応答。header = 要求のネットワーク番号~要求先局番号(5バイト)"""
    body = end_code.to_bytes(2, "little") + data
    return b"\xD0\x00" + header + len(body).to_bytes(2, "little") + body


def handle_request(mem: PlcMemory, req: bytes, verbose: bool) -> bytes:
    header = req[2:7]
    command = int.from_bytes(req[11:13], "little")
    sub = int.from_bytes(req[13:15], "little")
    p = req[15:]

    if sub in (0x0002, 0x0003):      # iQ-R形式: デバイス番号4バイト + コード2バイト
        num, code, n = int.from_bytes(p[0:4], "little"), int.from_bytes(p[4:6], "little"), int.from_bytes(p[6:8], "little")
        rest = p[8:]
    elif sub in (0x0000, 0x0001):    # Q/L形式: デバイス番号3バイト + コード1バイト
        num, code, n = int.from_bytes(p[0:3], "little"), p[3], int.from_bytes(p[4:6], "little")
        rest = p[6:]
    else:
        return build_response(header, ERR_COMMAND)

    if code not in DEVICE_CODES:
        return build_response(header, ERR_DEVICE)
    name, kind = DEVICE_CODES[code]
    area = getattr(mem, name)
    if num + n > len(area) or n == 0:
        return build_response(header, ERR_DEVICE)
    bit_unit = sub in (0x0001, 0x0003)
    label = f"{name}{num:X}" if name == "W" else f"{name}{num}"

    if command == 0x0401 and not bit_unit:          # ワード単位の一括読出
        if kind == "bit":                            # ビットデバイスを16点ずつ読む
            words = [sum(area[num + 16 * i + b] << b for b in range(16)) for i in range(n)]
        else:
            words = area[num:num + n]
        if verbose:
            print(f"  読出 {label} x{n}ワード -> {[f'0x{w:04X}' for w in words]}")
        return build_response(header, END_OK, b"".join(w.to_bytes(2, "little") for w in words))

    if command == 0x0401 and bit_unit:              # ビット単位の一括読出(2点で1バイト)
        if kind != "bit":
            return build_response(header, ERR_COMMAND)
        bits = area[num:num + n] + [0]
        data = bytes((bits[i] << 4) | bits[i + 1] for i in range(0, n, 2))
        if verbose:
            print(f"  読出 {label} x{n}点 -> {area[num:num + n]}")
        return build_response(header, END_OK, data)

    if command == 0x1401 and not bit_unit and kind == "word":   # ワード単位の一括書込
        if len(rest) != 2 * n:
            return build_response(header, ERR_REQUEST)
        values = [int.from_bytes(rest[2 * i:2 * i + 2], "little") for i in range(n)]
        area[num:num + n] = values
        print(f"  書込 {label} x{n}ワード <- {values}")
        return build_response(header, END_OK)

    return build_response(header, ERR_COMMAND)


async def serve_client(reader, writer, mem: PlcMemory, verbose: bool) -> None:
    peer = writer.get_extra_info("peername")
    print(f"接続: {peer}")
    try:
        while True:
            head = await reader.readexactly(9)            # サブヘッダ~要求データ長
            if head[0:2] != b"\x50\x00":
                print("3Eフレーム(バイナリ)以外の電文を受信したので切断します")
                break
            length = int.from_bytes(head[7:9], "little")
            body = await reader.readexactly(length)       # 監視タイマ + 要求データ
            writer.write(handle_request(mem, head + body, verbose))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        print(f"切断: {peer}")
        writer.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="MELSEC iQ-R MCプロトコル(3E/バイナリ)シミュレータ")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5010)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--verbose", action="store_true", help="読み書きされたデバイスを表示")
    args = ap.parse_args()

    mem = PlcMemory()
    asyncio.create_task(run_line(mem, args.interval))
    server = await asyncio.start_server(lambda r, w: serve_client(r, w, mem, args.verbose), args.host, args.port)
    print(f"MCプロトコル シミュレータ起動: {args.host}:{args.port}(3Eフレーム/バイナリ/TCP)  Ctrl+Cで終了")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
