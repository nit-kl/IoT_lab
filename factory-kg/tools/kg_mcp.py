r"""工場ナレッジグラフの MCP サーバー（各自の PC で動かす。決定事項: まずはローカル、リモート化を見据えた設計）。

    python tools/kg_mcp.py --owner kojima                      # stdio（Claude Code・Claude Desktop などから起動される）
    python tools/kg_mcp.py --owner kojima --no-sparql          # sparql_query ツールを出さない
    python tools/kg_mcp.py --owner kojima --http --port 8766   # Streamable HTTP（http://127.0.0.1:8766/mcp。リモート化の動作確認用）

Claude Code への登録例:
    claude mcp add factory-kg -- <factory-kg のフルパス>\.venv\Scripts\python.exe <factory-kg のフルパス>\tools\kg_mcp.py --owner kojima

aws login の認証情報で REST API（と SPARQL）を SigV4 署名して呼ぶ。stdio では標準出力を MCP の通信に使うので、
ログは標準エラーに出す。
"""

import argparse
import json
import sys

from kgapi import ApiError, KgClient
from kgmcp_tools import BackendError, build_server


class LocalBackend:
    """REST API・SPARQL を aws login の認証情報で署名して呼ぶ。"""

    def __init__(self, owner, region):
        self.client = KgClient(owner, region)

    def rest(self, path, params=None):
        try:
            return self.client.rest(path, params)
        except ApiError as e:
            raise BackendError(str(e)) from None

    def sparql(self, query):
        try:
            result = self.client.query(query)
        except ApiError as e:
            raise BackendError(str(e)) from None
        return result if isinstance(result, dict) else json.loads(result)


def main():
    ap = argparse.ArgumentParser(description="工場ナレッジグラフの MCP サーバー")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--region", default="ap-northeast-1")
    ap.add_argument("--no-sparql", action="store_true", help="sparql_query ツールを出さない")
    ap.add_argument("--http", action="store_true", help="stdio ではなく Streamable HTTP で待ち受ける（127.0.0.1 のみ）")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()

    backend = LocalBackend(args.owner, args.region)
    server = build_server(backend, enable_sparql=not args.no_sparql)
    print(f"factory-kg MCP: REST {backend.client.api_base}（sparql_query: {'なし' if args.no_sparql else 'あり'}）", file=sys.stderr)
    if args.http:
        import uvicorn
        app = server.streamable_http_app(stateless_http=True, json_response=True, host="127.0.0.1")
        print(f"Streamable HTTP: http://127.0.0.1:{args.port}/mcp", file=sys.stderr)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
