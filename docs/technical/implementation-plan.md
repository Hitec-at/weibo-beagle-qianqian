# 微博站点 · 比格钱钱 计划文档

> 状态：方案已确认，进入实现。

## 1. 目标与范围

- 对象：仅检索与推荐 `@比格钱钱`（UID 3866429224，微博认证萌宠博主）的公开微博。
- 三类检索视图：
  1. 热门迷因合集：关键词命中（dsnmd / 吃屎 / 垃圾桶 / 抢小孩零食 及可扩展同义词），按热度排序。
  2. 与 `@比格犬受害者联盟` 的互动：正文提及或转发该账号。
  3. 高清美图：含图 + 非视频 + 非 live + 转评总数（转+评）> 200，适配明信片 / 手机封面场景。
- 数据规模：尽量全量；先做一次快照，保留增量刷新入口。

## 2. 目录结构

```text
code/weibo-beagle-qianqian/
  docs/technical/    # 技术文档
    implementation-plan.md # 本文件
  README.md          # 使用说明
  .gitignore         # 忽略 .env / data/ / 缓存
  .env.example       # 环境变量模板（真实 .env 不提交）
  requirements.txt
  config.py          # 配置、关键词、筛选阈值
  store.py           # SQLite 存储与查询
  fetch_weibo.py     # 抓取脚本（快照 + 增量 + probe）
    cookie-sop.md    # Cookie 写入 SOP
  app/               # 后端服务（FastAPI）
  data/              # SQLite（不提交）
```

## 3. 数据获取与反爬设计（重点）

### 3.1 原则

- 绝不自动化登录。用户从已登录浏览器导出 Cookie（`SUB`/`SUBP`/`_T_WM` 等），脚本只复用该 Cookie。
- 仅用户本机 / 家庭 IP 运行；不在数据中心 IP 上抓取。
- 单进程、单线程、串行请求，不并发、不使用 Selenium/Playwright 驱动真实浏览器。
- 全文仅入库用于本地检索索引；公开展示只输出短引文，不整篇复制。

### 3.2 接口

- 时间线：`https://weibo.com/ajax/statuses/mymblog?uid=<uid>&page=N&feature=0`（weibo.com PC 端）
- 详情/图片：`https://weibo.com/ajax/statuses/show?id=<mid>`（取 `pic_infos` 缩略/大图 URL，原图不落盘）
- 长文正文：`https://weibo.com/ajax/statuses/longtext?id=<mid>`
- 互动判定：正文文本或转发源匹配 `比格犬受害者联盟`。

### 3.3 风控策略（防封号）

- 请求头：真实 UA + `Referer: https://m.weibo.cn/` + `X-Requested-With: XMLHttpRequest` + `MWeibo-Pwa: 1` + Cookie。
- 频率：单请求间隔 4–8 秒（随机抖动）；每 30 页休眠 2–5 分钟；单日请求上限默认 500 次。
- 异常退避：命中 403 / 418 / 429 / 432 / 302 / 验证码 / 返回非 JSON 时立即停止，不短间隔重试；Cookie 失效（ok=0/-100）单独报错并停止。
- 断点续抓：以微博 `id` 为幂等键，已抓跳过；检查点存 `meta` 表（last_id / last_page）。

### 3.4 增量刷新

- `python fetch_weibo.py --mode snapshot`：全量快照，翻页到底或到停止位。
- `python fetch_weibo.py --mode incremental`：自 `last_id` 向前只取新增，按 `since_id` 增量。
- `python fetch_weibo.py --mode probe`：校验 Cookie 并打印首条样本（用于核对视频 / live 字段）。
- 支持 `--resume` 从检查点继续；`--limit N` 单次页数上限。

## 4. 数据模型（SQLite）

- `posts(id, mid, uid, screen_name, created_at, ts, text, text_plain, reposts_count, comments_count, attitudes_count, source, is_longtext, is_video, is_live, has_pics, pics_json, retweeted_user, raw_json, fetched_at)`
- `pics_json`：图片元数据（缩略 / 大图 URL），仅用于预览，原图不落盘。
- `meta(key, value)`：检查点与运行状态。

## 5. 检索与推荐

- 全文检索：SQLite `LIKE` 包含匹配（中文无分词，该规模下正确且够用）。
- 合集命中：关键词表（`config.py` 可配置）匹配正文，按热度排序。
- 热度分：`likes + 5*reposts + 3*comments`（可调）。
- 推荐位：首页按热度展示，三类视图各自独立筛选。
- 高清图视图：含图 + 非视频 + 非 live + 转评总数（转+评）> 阈值（默认 200）。

## 6. 后端与部署

- 后端：Python 3.11 + FastAPI + SQLite + uvicorn；服务端渲染模板（Jinja2），无前端构建。
- 本地运行：`uvicorn app.main:app`；后续上云提供 Dockerfile / docker-compose。
- 数据目录与 `.env` 均不纳入版本控制。

## 7. 版权与合规策略（已确认）

- 站点公开部分只做「引用」：短引文 + 缩略图预览 + 跳原微博；正文不整篇复制。
- 原图不落盘：数据库只保存缩略 / 大图 URL 做预览；「明信片 / 手机封面」由用户个人在原微博自取，站点不提供原图下载。
- 始终署名 `@比格钱钱` 并附原文链接；页面加免责声明与侵权即删通道。
- 若将来公开上线或商用（如售卖明信片），应先取得 `@比格钱钱` 授权。

## 8. 已确认

- [x] 后端技术栈：FastAPI + SQLite（`LIKE` 检索）。
- [x] Cookie：由用户按 `docs/technical/cookie-sop.md` 自行写入本机 `.env`，不在对话中明文传递。
- [x] 版权：公开只引用（短引文 + 缩略图预览 + 跳原链），原图不落盘。
- [x] 高清图筛选：含图 + 非视频 + 非 live + 转评总数 > 200。

## 9. 后续方向

- [x] 筛选管线工具化：`wbq.py` CLI + `filters.py` 共享筛选逻辑，Web 与 CLI 复用同一套 WHERE 条件。
- [ ] 全量历史：`fetch --mode snapshot --resume` 多轮跑到头（日限 500 次，断点续抓）。
- [ ] 评论图片：需接入评论接口（独立数据源、额外请求量），待全量微博入库后再评估。
- [ ] 高清图阈值再定：实测「非 live + 非视频 + 转评>200」在近 325 条命中 0，需用户确认阈值口径。
