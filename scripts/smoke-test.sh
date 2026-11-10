#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${1:-http://127.0.0.1:8000}"
DB_PATH="${2:-$ROOT_DIR/data/weibo.db}"
BASE_URL="${BASE_URL%/}"

request() {
  local path="$1"
  local output="${2:-/dev/null}"
  curl --fail --silent --show-error \
    --retry 5 --retry-delay 2 --retry-all-errors \
    --max-time 30 -o "$output" "$BASE_URL$path"
}

echo "==> 基础接口：$BASE_URL"
request "/healthz"
request "/"
request "/memes"
request "/memes/all"
request "/interactions"
request "/photos"
request "/search?q=%E5%9E%83%E5%9C%BE%E6%A1%B6"

POST_ID="$(python3 - "$DB_PATH" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
try:
    row = conn.execute("SELECT id FROM posts ORDER BY ts DESC LIMIT 1").fetchone()
    print(row[0] if row else "")
finally:
    conn.close()
PY
)"

if [[ -z "$POST_ID" ]]; then
  echo "数据库中没有可用于验收的微博。" >&2
  exit 1
fi
request "/post/$POST_ID"
echo "post_id=$POST_ID"

PIC_URL="$(python3 - "$DB_PATH" <<'PY'
import json
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
try:
    rows = conn.execute(
        "SELECT pics_json FROM posts WHERE has_pics = 1 AND pics_json != '' LIMIT 50"
    ).fetchall()
finally:
    conn.close()

for (raw,) in rows:
    try:
        pics = json.loads(raw or "[]")
    except ValueError:
        continue
    for pic in pics:
        url = pic.get("thumb") or pic.get("url") or ""
        if url.startswith(("https://", "http://")) and ".sinaimg.cn" in url:
            print(url)
            raise SystemExit(0)
PY
)"

if [[ -n "$PIC_URL" ]]; then
  TMP_BODY="$(mktemp)"
  trap 'rm -f "$TMP_BODY"' EXIT
  RESULT="$(curl --fail --silent --show-error \
    --retry 3 --retry-delay 2 --retry-all-errors --max-time 30 \
    -G --data-urlencode "u=$PIC_URL" \
    -o "$TMP_BODY" -w '%{http_code} %{content_type}' \
    "$BASE_URL/img")"
  CODE="${RESULT%% *}"
  CONTENT_TYPE="${RESULT#* }"
  if [[ "$CODE" != "200" || "$CONTENT_TYPE" != image/* ]]; then
    echo "图片代理验收失败：$RESULT" >&2
    exit 1
  fi
  echo "image_proxy=ok"
fi

echo "smoke=ok"
