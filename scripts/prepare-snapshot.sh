#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${DB_PATH:-data/weibo.db}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-dist}"
UPLOAD=1

if [[ "${1:-}" == "--no-upload" ]]; then
  UPLOAD=0
elif [[ $# -gt 0 ]]; then
  echo "用法：$0 [--no-upload]" >&2
  exit 2
fi

command -v sqlite3 >/dev/null 2>&1 || {
  echo "未找到 sqlite3，请先安装。" >&2
  exit 1
}
command -v gzip >/dev/null 2>&1 || {
  echo "未找到 gzip。" >&2
  exit 1
}
[[ -f "$DB_PATH" ]] || {
  echo "数据库不存在：$DB_PATH" >&2
  exit 1
}

echo "==> 生成 SQLite 一致性副本"
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
TMP_DB="$(mktemp "${TMPDIR:-/tmp}/weibo-snapshot.XXXXXX")"
trap 'rm -f "$TMP_DB"' EXIT
sqlite3 "$DB_PATH" ".backup '$TMP_DB'"

echo "==> 检查快照完整性"
INTEGRITY="$(sqlite3 "$TMP_DB" "PRAGMA integrity_check;")"
if [[ "$INTEGRITY" != "ok" ]]; then
  echo "SQLite integrity_check 失败：$INTEGRITY" >&2
  exit 1
fi

POSTS="$(sqlite3 "$TMP_DB" "SELECT COUNT(*) FROM posts;")"
CATEGORIES="$(sqlite3 "$TMP_DB" "SELECT COUNT(*) FROM post_categories;")"
echo "posts=$POSTS categories=$CATEGORIES"

mkdir -p "$SNAPSHOT_DIR"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
RELEASE_TAG="${RELEASE_TAG:-data-$STAMP}"
if [[ ! "$RELEASE_TAG" =~ ^data-[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "RELEASE_TAG 必须是不可变的 data-YYYYMMDDTHHMMSSZ：$RELEASE_TAG" >&2
  exit 1
fi
ASSET="weibo-${STAMP}.db.gz"
ASSET_PATH="$SNAPSHOT_DIR/$ASSET"

echo "==> 压缩数据库"
gzip -9 -n -c "$TMP_DB" >"$ASSET_PATH"

if command -v sha256sum >/dev/null 2>&1; then
  SHA256="$(sha256sum "$ASSET_PATH" | awk '{print $1}')"
else
  SHA256="$(shasum -a 256 "$ASSET_PATH" | awk '{print $1}')"
fi
printf '%s  %s\n' "$SHA256" "$ASSET" >"$ASSET_PATH.sha256"

echo "snapshot=$ASSET"
echo "sha256=$SHA256"
echo "size=$(du -h "$ASSET_PATH" | awk '{print $1}')"
echo "release=$RELEASE_TAG"
printf 'RELEASE_TAG=%s\nSNAPSHOT_ASSET=%s\nSNAPSHOT_SHA256=%s\n' \
  "$RELEASE_TAG" "$ASSET" "$SHA256" >"$SNAPSHOT_DIR/latest.env"

if [[ "$UPLOAD" -eq 0 ]]; then
  echo "已跳过上传。"
  exit 0
fi

command -v gh >/dev/null 2>&1 || {
  echo "未找到 gh CLI。" >&2
  exit 1
}
gh auth status >/dev/null 2>&1 || {
  echo "GitHub CLI 尚未登录，请先执行：gh auth login -h github.com" >&2
  exit 1
}

echo "==> 创建不可变数据 Release：$RELEASE_TAG"
# Bash 3.2 in UTF-8 locales can absorb a following multibyte byte into the
# variable name, so keep explicit braces around values embedded in Chinese text.
gh release create "$RELEASE_TAG" "$ASSET_PATH" "$ASSET_PATH.sha256" \
  --title "微博数据快照 ${STAMP}" \
  --notes "数据文件：${ASSET}；发布时间：${STAMP}。" \
  --prerelease

echo
echo "下一步触发部署："
echo "./deploy.sh"
