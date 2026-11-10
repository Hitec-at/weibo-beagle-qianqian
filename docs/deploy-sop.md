# 云端部署 SOP（GitHub Actions · Fly.io）

采用“GitHub 手动部署”方案：SQLite 快照保存在私有 GitHub Release，Actions 在构建时
下载、校验并烤入镜像；Fly.io 只运行一个无持久卷、无微博 Cookie 的只读实例。

## 一、首次部署

1. 登录 GitHub CLI：

   ```bash
   gh auth login -h github.com
   ```

2. 创建或登录 Fly.io 账号，绑定付款方式，并使用个人组织。在控制台设置约 `$10/月`
   的账单告警。
3. 确认 `fly.toml` 中的 `app` 名称全局唯一。默认是 `wbq-kerry-tech`，如果不唯一，
   改成自己的名称。
4. 在 GitHub 仓库的 Actions Secrets 中增加 `FLY_API_TOKEN`。Token 可通过 Fly
   控制台创建，不要写入代码或聊天。
5. 生成本地数据快照并上传：

   ```bash
   ./scripts/prepare-snapshot.sh
   ```

   脚本会检查 SQLite 完整性、生成压缩包和 SHA256，并上传到 `data-latest` Release。
6. 触发部署：

   ```bash
   ./deploy.sh
   ```

   Actions 会依次完成快照校验、容器构建、本地冒烟测试、Fly 发布和公网验收。
7. 查看运行状态：

   ```bash
   gh run list --workflow deploy.yml --limit 1
   gh run watch
   ```

## 二、公网验收

Actions 会自动请求以下地址：

- `/healthz`
- `/`
- `/memes`
- `/memes/all`
- `/interactions`
- `/photos`
- `/search?q=垃圾桶`
- 数据库中的真实 `/post/{id}`
- 数据库中的真实新浪图片 `/img?u=...`

任一核心检查失败时，工作流失败，不会把该次运行标记为成功。

部署后人工检查：

1. 在电脑和手机流量下各打开一次首页。
2. 检查图片加载速度，以及点击图片是否跳转原微博。
3. 打开一个视频微博，确认可播放或显示“视频直链已过期，打开原微博或视频原页”。
4. 如果东京节点访问新浪图床明显异常，把 `fly.toml` 的 `primary_region` 改为
   `sin`，重新触发部署。

## 三、日常更新

1. 本地抓取和分类：

   ```bash
   .venv/bin/python wbq.py fetch --mode snapshot --resume
   .venv/bin/python wbq.py stats
   ```

   遵守反爬限制：默认 4–8 秒间隔、每 30 页休眠、单日 500 次上限。
2. 生成并上传快照：

   ```bash
   ./scripts/prepare-snapshot.sh
   ```
3. 需要发布时再手动触发：

   ```bash
   ./deploy.sh
   ```

上传快照不会自动发布，只有执行第二步才会产生新版本。

## 四、回滚与故障处理

```bash
flyctl releases
flyctl rollback
flyctl logs
```

Actions 摘要会记录本次快照文件名、SHA256 和公网地址。回滚使用 Fly 上一个 release，
数据库快照仍可从 GitHub Release 重新构建。

## 五、安全边界

- `.env`、微博 Cookie 和 `data/` 不进入 Git。
- 运行时镜像只包含已校验的 SQLite 快照，不包含抓取工具。
- `/img` 只允许精确匹配新浪图床域名；拒绝重定向和非图片响应。
- 图片内存缓存限制为约 24 MB，避免 256 MB Machine 被缓存撑满。
- 公网默认设置 `ENABLE_VIDEO_REFRESH=0`，不会在服务器保存或使用微博 Cookie。
- 每日抓取任务只运行在本地，不访问公网 Fly 实例。
