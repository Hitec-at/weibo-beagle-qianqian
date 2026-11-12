# 参与项目

## 提交问题

先创建 Issue，说明复现步骤、受影响页面或功能和相关数据快照。缺陷无法复现时，
说明已知条件缺口。

## 提交代码

- 从最新 `main` 创建 `feat/`、`fix/` 或 `chore/` 分支。
- 一个分支只处理一个主要目标。
- 通过 GitHub PR 合入，不在 CI 失败时合并。
- 提交前运行：

  ```bash
  python -m compileall -q app baseline.py comment_pilot.py config.py classify.py fetch_weibo.py filters.py memes.py store.py wbq.py
  python -m unittest discover -s tests -v
  ```

## 数据与凭据

不要提交 `.env`、Cookie、Token、SQLite 数据库、`data/`、`dist/` 或 `*.private`
文件。微博抓取必须遵守项目默认的请求间隔、休眠和每日请求上限。

生产部署和不可变数据 Release 的操作规则见
[`AGENTS.md`](AGENTS.md) 与 [`docs/technical/deploy-sop.md`](docs/technical/deploy-sop.md)。
