#!/usr/bin/env bash
# 触发 GitHub Actions，以最近一次 prepare-snapshot.sh 生成的快照部署。
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f dist/latest.env ]]; then
  echo "未找到 dist/latest.env，请先执行：./scripts/prepare-snapshot.sh" >&2
  exit 1
fi

# shellcheck disable=SC1091
source dist/latest.env

if [[ -z "${RELEASE_TAG:-}" ]]; then
  echo "dist/latest.env 缺少 RELEASE_TAG，请重新执行：./scripts/prepare-snapshot.sh" >&2
  exit 1
fi
if [[ ! "$RELEASE_TAG" =~ ^data-[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "RELEASE_TAG 必须是不可变的 data-YYYYMMDDTHHMMSSZ：$RELEASE_TAG" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "未检测到 GitHub CLI。" >&2
  exit 1
fi
gh auth status >/dev/null 2>&1 || {
  echo "GitHub CLI 尚未登录，请执行：gh auth login -h github.com" >&2
  exit 1
}

echo "==> 触发 GitHub Actions 部署"
echo "snapshot=$SNAPSHOT_ASSET"
echo "sha256=$SNAPSHOT_SHA256"
echo "release=$RELEASE_TAG"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-$(git branch --show-current)}"
if [[ -z "$DEPLOY_BRANCH" ]]; then
  echo "无法确定当前分支，请设置 DEPLOY_BRANCH。" >&2
  exit 1
fi
echo "branch=$DEPLOY_BRANCH"
gh workflow run deploy.yml \
  --ref "$DEPLOY_BRANCH" \
  -f snapshot_asset="$SNAPSHOT_ASSET" \
  -f sha256="$SNAPSHOT_SHA256" \
  -f release_tag="$RELEASE_TAG"

echo
echo "部署已提交。查看运行状态："
echo "gh run list --workflow deploy.yml --limit 1"
