# 项目开发与部署守则

本文件约束本仓库的需求、分支、发布、部署和回滚流程。涉及安全、数据可复现性或生产发布的改动，必须遵守以下规则。

## 需求与缺陷

1. 缺陷和需求先创建 Issue，再开始代码改动。
2. Issue 必须写明复现步骤、受影响的页面或功能，以及用于复现的相关数据快照；无法复现时说明已知条件缺口。

## 分支与 PR

1. 分支名统一使用 `feat/xxx`、`fix/xxx` 或 `chore/xxx`，不得使用 `codex/` 前缀。
2. 一个分支只对应一个主要目标；无关清理、格式化或功能不得搭车提交。
3. 所有代码改动必须走 PR。PR 必须通过 CI，不得在 CI 失败时合入 `main`。
4. PR 和合入 `main` 都不自动部署生产环境。
5. 分支必须基于最新 `main`。合并前如发现落后或冲突，先 rebase 到最新 `main`，解决冲突并重新通过 CI。

## Release 与部署绑定

1. 正式代码发布从 `main` 创建不可变 `v*` Release，并使用语义版本。
2. 微博数据快照使用不可变 `data-*` Release。`data-latest` 只能作为便利别名，不能作为审计或可复现部署的依据。
3. 每次生产部署必须固定代码提交、数据 Release、资产文件名和 SHA256；绑定信息建议写入 `deploy/snapshot.json`。
4. 代码发布使用 `v*` Release；仅更新数据时可不创建代码版本标签，但必须引用不可变 `data-*` Release 并手动触发部署。
5. 生产部署只能由代码 Release 的 `published` 事件触发或手动触发，不得在普通 `push` 时部署。

建议的部署清单格式：

```json
{
  "commit": "<40位提交哈希>",
  "code_release": "v1.0.0",
  "data_release": "data-20261110T160000Z",
  "asset_name": "weibo-20261110T160000Z.db.gz",
  "sha256": "<64位SHA256>"
}
```

仅更新数据时，`code_release` 可记录当前生产代码对应的 Release，但不得使用可变的 `data-latest` 代替 `data_release`。

## 工作流职责

1. `ci.yml` 只负责 PR 和 `main` 验证，不接触 Fly 凭据。
2. `deploy.yml` 只负责 Release 或手动触发部署。
3. 可选 `rollback.yml` 只负责历史版本回滚。
4. 不在 PR 阶段默认创建 Fly 预览环境；只有后续明确需要时才增加。

## Secret 与权限

1. GitHub 生产 Secret 放在 `production` Environment。
2. Fly Token 使用仅限目标应用的最小权限，并设定到期时间、定期轮换。
3. Cookie、Token、数据库和本地 `.env` 不得进入 Git、日志或构建产物。

## 部署验收

每次部署后必须验收：

- `/healthz` 健康检查通过。
- 首页、分类页、搜索页和真实 `/post/{id}` 详情页返回成功。
- `/img` 图片代理返回真实图片，且非新浪图床域名被拒绝。
- Actions 和人工验收均成功后才能标记发布完成；任一核心检查失败，本次运行不得标记为成功。

## 回滚与发布记录

1. 回滚优先重新部署历史代码 Release 和与之绑定的数据 Release。
2. 不得只依赖 Fly 回滚并假设它会恢复正确的 SQLite 快照。
3. 发布记录至少包含代码提交、代码 Release、数据 Release、资产 SHA256、Fly release 和公网验收结果。
