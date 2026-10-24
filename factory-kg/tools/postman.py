"""Postman のコレクションと Environment を作る。

    python tools/postman.py collection              # postman/xcp-factorykg.postman_collection.json（コミットする）
    python tools/postman.py env --owner kojima      # postman/xcp-factorykg-<owner>.postman_environment.json（認証情報入り。コミットしない）

コレクションの SPARQL は queries/*.rq から作るので、CLI（tools/sparql.py）と同じクエリになる。
認証情報は一時的なもの。Postman で 403（token expired など）が出たら env を作り直してインポートし直す。
"""

import argparse
import json

from kgapi import KgClient
from kgcommon import QUERIES_DIR, ROOT
from kgnaming import stack_name
from query import PREFIX_HEADER

OUT_DIR = ROOT / "postman"
COLLECTION = OUT_DIR / "xcp-factorykg.postman_collection.json"


def test(*lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]


def request(name, method, path, body=None, content_type=None, query=None, auth=None, tests=None, description=None):
    url = {"raw": "{{sparqlUrl}}" + path, "host": ["{{sparqlUrl}}"], "path": [p for p in path.split("?")[0].split("/") if p]}
    if query:
        url["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query)
        url["query"] = [{"key": k, "value": v} for k, v in query]
    req = {"method": method, "url": url, "header": [{"key": "Accept", "value": "application/sparql-results+json"}]}
    if body is not None:
        req["header"].append({"key": "Content-Type", "value": content_type})
        req["body"] = {"mode": "raw", "raw": body}
    if auth:
        req["auth"] = auth
    if description:
        req["description"] = description
    item = {"name": name, "request": req}
    if tests:
        item["event"] = test(*tests)
    return item


def build_collection():
    ok = "pm.test('200 が返る', () => pm.response.to.have.status(200));"
    items = [
        request("1. status（公開中のグラフ）", "GET", "/status",
                tests=[ok, "pm.test('トリプルがある', () => pm.expect(pm.response.json().triples).to.be.above(0));"]),
        request("2. status（S3 から読み直す）", "GET", "/status", query=[("reload", "1")], tests=[ok],
                description="publish 直後に、新しいグラフが使われているかを確かめる。"),
    ]
    for i, path in enumerate(sorted(QUERIES_DIR.glob("*.rq")), start=3):
        sparql = path.read_text(encoding="utf-8")
        title = sparql.splitlines()[0].lstrip("# ").strip()
        items.append(request(f"{i}. {path.stem}（{title}）", "POST", "/sparql", body=PREFIX_HEADER + sparql,
                             content_type="application/sparql-query",
                             tests=[ok, "pm.test('結果がある', () => pm.expect(pm.response.json().results.bindings.length).to.be.above(0));"]))
    n = len(items) + 1
    items += [
        request(f"{n}. GET で問い合わせる（?query=）", "GET", "/sparql",
                query=[("query", "SELECT%20(COUNT(*)%20AS%20%3Fn)%20WHERE%20%7B%20%3Fs%20%3Fp%20%3Fo%20%7D")], tests=[ok]),
        request(f"{n + 1}. 更新は拒否される（→ 403）", "POST", "/sparql", body="INSERT DATA { <urn:a> <urn:b> <urn:c> }",
                content_type="application/sparql-update",
                tests=["pm.test('403 が返る', () => pm.response.to.have.status(403));"]),
        request(f"{n + 2}. 構文エラー（→ 400）", "POST", "/sparql", body="SELEC * WHERE {}",
                content_type="application/sparql-query",
                tests=["pm.test('400 が返る', () => pm.response.to.have.status(400));"]),
        request(f"{n + 3}. 署名なし（→ 403）", "GET", "/status", auth={"type": "noauth"},
                tests=["pm.test('403 が返る（IAM 認証が効いている）', () => pm.response.to.have.status(403));"]),
    ]
    return {
        "info": {
            "name": "xcp-factorykg SPARQL",
            "description": "工場ナレッジグラフの SPARQL エンドポイント（Lambda Function URL、IAM 認証、読み取り専用）。\n\n"
                           "1. `python tools/postman.py env --owner <owner>` で Environment を作ってインポートする\n"
                           "2. 右上の Environment で `xcp-factorykg-<owner>` を選ぶ\n3. 各リクエストを Send\n\n"
                           "このファイルは `python tools/postman.py collection` で queries/*.rq から生成している（手で編集しない）。",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "awsv4", "awsv4": [
            {"key": "accessKey", "value": "{{accessKeyId}}", "type": "string"},
            {"key": "secretKey", "value": "{{secretAccessKey}}", "type": "string"},
            {"key": "sessionToken", "value": "{{sessionToken}}", "type": "string"},
            {"key": "region", "value": "{{region}}", "type": "string"},
            {"key": "service", "value": "lambda", "type": "string"},
        ]},
        "item": items,
    }


def build_env(owner, region):
    client = KgClient(owner, region)
    creds = client.session.get_credentials()
    frozen = creds.get_frozen_credentials()
    values = [("sparqlUrl", client.base, "default"), ("region", region, "default"),
              ("accessKeyId", frozen.access_key, "secret"), ("secretAccessKey", frozen.secret_key, "secret"),
              ("sessionToken", frozen.token or "", "secret")]
    env = {"name": stack_name(owner), "values": [{"key": k, "value": v, "type": t, "enabled": True} for k, v, t in values],
           "_postman_variable_scope": "environment"}
    expiry = getattr(creds, "_expiry_time", None)
    return env, client.base, expiry


def main():
    ap = argparse.ArgumentParser(description="Postman のコレクション・Environment を作る")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("collection", help="コレクションを queries/*.rq から作る")
    p = sub.add_parser("env", help="Environment（URL・認証情報）を作る")
    p.add_argument("--owner", required=True)
    p.add_argument("--region", default="ap-northeast-1")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    if args.command == "collection":
        collection = build_collection()
        COLLECTION.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"作成しました: {COLLECTION}（{len(collection['item'])} リクエスト）")
        return
    env, base, expiry = build_env(args.owner, args.region)
    out = OUT_DIR / f"{env['name']}.postman_environment.json"
    out.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"作成しました: {out}")
    print(f"SPARQL の URL: {base}")
    if expiry:
        print(f"認証情報の有効期限: {expiry.astimezone().isoformat(timespec='minutes')}（切れたら再実行してインポートし直す）")


if __name__ == "__main__":
    main()
