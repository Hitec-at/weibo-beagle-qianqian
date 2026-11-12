# 安全说明

## 报告安全问题

请通过仓库 **Security** 页面的“Report a vulnerability”私下报告安全问题。
不要在公开 Issue 中提交 Cookie、Token、生产账号、数据库转储或其他敏感信息。

报告时请说明受影响版本、复现步骤、影响范围和建议修复方式。维护者会先确认问题，
再协调修复与披露时间。

## 敏感信息边界

- `.env`、`*.cookie`、`*.private` 和本地数据库不得提交到 Git。
- GitHub Actions 的生产 Secret 只放在 `production` Environment。
- 公开数据 Release 只应包含有意公开的微博站点快照，不得包含抓取 Cookie、
  登录凭据、生产 Token 或本地私有配置。
- 发现误提交凭据时，应立即轮换凭据并清理公开发布资产；仅删除最新提交不足以撤销暴露。
