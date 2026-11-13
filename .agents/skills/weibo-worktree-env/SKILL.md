---
name: weibo-worktree-env
description: "Prepare non-main Git worktrees for Weibo data work by safely copying the configured .env from the repository's main worktree before commands need WEIBO_COOKIE or other project environment values."
---

# 微博 Worktree 环境

在 Git worktree 中开始涉及微博数据读取的开发或运行时，先确认本地 `.env` 已准备好。

## 何时使用

当当前目录不是主工作树，且任务即将执行以下任一操作时使用：

- 调用微博接口，例如 `wbq.py fetch`、`baseline probe`、评论抓取或视频刷新。
- 启动依赖 `WEIBO_COOKIE`、`WEIBO_UID`、`DB_PATH` 或其他项目环境变量的程序。
- 运行需要读取真实微博数据的集成测试或运维命令。

普通前端样式、纯离线单元测试或明确不需要微博环境的修改，不要触发本 skill。

## 准备步骤

1. 确认当前目录是 Git worktree，并解析主工作树：

   ```bash
   CURRENT_ROOT="$(git rev-parse --show-toplevel)"
   COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
   [[ "$COMMON_GIT_DIR" = /* ]] || COMMON_GIT_DIR="$CURRENT_ROOT/$COMMON_GIT_DIR"
   COMMON_GIT_DIR="$(cd "$COMMON_GIT_DIR" && pwd -P)"
   MAIN_ROOT="$(dirname "$COMMON_GIT_DIR")"
   ```

2. 如果 `CURRENT_ROOT` 与 `MAIN_ROOT` 相同，不需要复制，直接继续任务。

3. 检查 `"$MAIN_ROOT/.env"` 存在且非空。不要读取或打印其中的值。

4. 检查当前 worktree：

   ```bash
   test -f "$CURRENT_ROOT/.env"
   ```

   - 文件不存在或为空：从 `"$MAIN_ROOT/.env"` 复制。
   - 文件已存在且非空：不要覆盖。仅在用户明确要求刷新环境时再更新。

5. 优先使用本 skill 自带的脚本；它会检查主工作树、来源 `.env`、忽略规则和目标文件：

   ```bash
   .agents/skills/weibo-worktree-env/scripts/sync-env.sh
   ```

   只有用户明确要求刷新已有非空 `.env` 时才使用 `--refresh`。脚本不输出环境值。

6. 复制后只做无泄漏校验：

   ```bash
   git check-ignore -q .env
   git status --short -- .env
   ```

   第二条命令必须没有输出。若 `.env` 未被忽略，停止任务并先修正 `.gitignore`；不要把文件加入 Git。

## 安全约束

- 只从同一仓库的主工作树复制，不从其他项目或任意路径寻找 `.env`。
- 不执行 `cat .env`，不输出 Cookie、Token、完整环境变量或文件内容。
- 不把 `.env` 复制到 `dist/`、Docker 构建上下文或日志目录。
- 不覆盖已存在的非空 `.env`，避免静默丢失本地覆盖项。
- 不提交、暂存、上传或缓存 `.env`。
- 如果复制失败或 `.env` 未配置 `WEIBO_COOKIE`，报告缺失的变量名，不要伪造凭据。

## 完成条件

当前 worktree 根目录存在仅当前用户可读的 `.env`，`git status` 不显示它，然后才可继续读取微博数据。
