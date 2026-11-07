"""メールの組み立てと、対応済みリンクの署名。"""

import base64
import hashlib
import hmac
import html

from notify.writer import NOTE

SUBJECT_MAX = 120


def sign(secret, event_id, expires):
    text = f"{event_id}|{expires}".encode()
    return base64.urlsafe_b64encode(hmac.new(secret, text, hashlib.sha256).digest()).decode().rstrip("=")


def verify(secret, event_id, expires, signature, now):
    if int(expires) < now:
        return False
    return hmac.compare_digest(sign(secret, event_id, expires), signature)


def subject_line(role, report):
    text = f"[{role}][{report.tag}] {report.subject}"
    return " ".join(text.split())[:SUBJECT_MAX]  # 改行が混ざるとヘッダーを壊すので 1 行にする


def build(role, report, ack_url=None):
    """(件名, テキスト本文, HTML 本文) を返す。グラフ由来の文字列は HTML として解釈させない。"""
    text = report.body
    page = "".join(f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>" for block in text.split("\n\n"))
    if ack_url:
        text += f"\n\n対応を始めたら記録してください:\n{ack_url}"
        page += f'<p><a href="{html.escape(ack_url, quote=True)}">対応を記録する</a></p>'
    text += f"\n\n--\n{NOTE}"
    page += f'<hr><p style="color:#666;font-size:12px">{html.escape(NOTE)}</p>'
    return subject_line(role, report), text, page
