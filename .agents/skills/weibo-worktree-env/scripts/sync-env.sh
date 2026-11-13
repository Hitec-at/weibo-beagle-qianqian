#!/usr/bin/env bash
set -euo pipefail

REFRESH=0
if [[ "${1:-}" == "--refresh" ]]; then
  REFRESH=1
elif [[ $# -gt 0 ]]; then
  echo "用法：$0 [--refresh]" >&2
  exit 2
fi

CURRENT_ROOT="$(git rev-parse --show-toplevel)"
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
[[ "$COMMON_GIT_DIR" = /* ]] || COMMON_GIT_DIR="$CURRENT_ROOT/$COMMON_GIT_DIR"
COMMON_GIT_DIR="$(cd "$COMMON_GIT_DIR" && pwd -P)"
MAIN_ROOT="$(dirname "$COMMON_GIT_DIR")"
SOURCE_ENV="$MAIN_ROOT/.env"
TARGET_ENV="$CURRENT_ROOT/.env"

if [[ "$CURRENT_ROOT" == "$MAIN_ROOT" ]]; then
  echo "当前目录就是主工作树，无需复制：$CURRENT_ROOT"
  exit 0
fi

if [[ ! -s "$SOURCE_ENV" ]]; then
  echo "主工作树的 .env 不存在或为空：$SOURCE_ENV" >&2
  exit 1
fi

if ! awk -F= '
  $1 == "WEIBO_COOKIE" {
    value = substr($0, index($0, "=") + 1)
    if (length(value) > 0) found = 1
  }
  END { exit(found ? 0 : 1) }
' "$SOURCE_ENV"; then
  echo "主工作树 .env 缺少非空 WEIBO_COOKIE" >&2
  exit 1
fi

if ! git -C "$CURRENT_ROOT" check-ignore -q .env; then
  echo "目标 worktree 的 .env 未被 Git 忽略，拒绝复制" >&2
  exit 1
fi

if [[ -s "$TARGET_ENV" && "$REFRESH" -ne 1 ]]; then
  echo "worktree 已存在非空 .env，未覆盖：$TARGET_ENV"
  exit 0
fi

umask 077
TMP_ENV="$(mktemp "$CURRENT_ROOT/.env.copy.XXXXXX")"
trap 'rm -f "$TMP_ENV"' EXIT
cp "$SOURCE_ENV" "$TMP_ENV"
chmod 600 "$TMP_ENV"
mv "$TMP_ENV" "$TARGET_ENV"
trap - EXIT

if [[ -n "$(git -C "$CURRENT_ROOT" status --short -- .env)" ]]; then
  echo ".env 出现在 git status 中，停止继续任务" >&2
  exit 1
fi

echo "已从主工作树准备 worktree 环境：$TARGET_ENV"
