# 微博站点 · 比格钱钱

仅检索与推荐 `@比格钱钱`（UID 3866429224）公开微博的本地站点。公开只做「引用」：短引文 + 缩略图预览 + 跳转原微博，原图不落盘。

## 快速开始

```bash
cd code/weibo-beagle-qianqian
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 按 docs/cookie-sop.md 把 Cookie 填入 .env
python fetch_weibo.py --mode probe        # 校验 Cookie
python fetch_weibo.py --mode snapshot     # 全量快照
uvicorn app.main:app --reload
```

浏览器打开 http://127.0.0.1:8000 。

## 抓取命令

- `--mode snapshot`：全量快照，翻页到底或到风控停止位。
- `--mode incremental`：自上次检查点只取新增。
- `--mode probe`：只发一次请求，校验 Cookie 并打印首条样本（核对视频 / live 字段）。
- `--resume`：从检查点继续；`--limit N`：本次最多抓 N 页（试跑用）。

## 检索视图

- 首页 `/`：按热度推荐。
- N宗罪 `/memes`：按人工维护的罪名/行为条目聚合，单个条目见 `/meme/{slug}`。
- 全部罪证 `/memes/all`：保留原有 dsnmd / 吃屎 / 垃圾桶 / 抢小孩零食 匹配。
- 互动 `/interactions`：正文或转发源出现 `比格犬受害者联盟`，并展示源微博正文、图片和可直接播放的视频。
- 高清图 `/photos`：含图 + 非视频 + 非 live + 转评总数（转+评）> 阈值（PHOTO_MIN_ENGAGEMENT，当前 100）。

所有列表卡片中的视频都可以直接播放；转发微博优先使用源微博视频，首次播放时如果直链过期会自动刷新。微博的混合媒体（`mix_media_info`）也受支持，文字、图片和视频会同时展示。

N宗罪条目在 `memes.seed.toml` 中人工维护；每个条目可以是一项热梗，也可以是一类反复出现的行为，如偷吃、变胖、上秤称重，并支持 `aliases`、`exclude`、`search_queries` 和多标签关联。修改后执行 `wbq.py memes-import` 重新关联。关键词与阈值在 `config.py` 中维护。

每个 tab 使用服务端分页：列表每页 20 条，高清图每页 24 条。抓取结束后会自动运行 `wbq.py classify`，把新入库微博归入热门、N宗罪、互动和高清图。

## 每日任务

- 每天 01:00 自动执行 `wbq.py fetch --mode snapshot --resume`，目标覆盖截至 2026-11-09 的存量微博（含更早历史）。
- 每次抓取结束或触发日限停止后，自动执行分类整理，把新入库微博写入四个 tab。
- 存量翻到底后输出“已覆盖截至 2026-11-09 的存量微博”，人工暂停定时任务即可。

高清图分类方案暂列 TODO，详见 `docs/roadmap.md`。当前优先级是补全微博内容并稳定热门、N宗罪、互动三个 tab。

## CLI 工具

筛选管线已抽成命令行（`wbq.py`），Web 与 CLI 共用同一套筛选逻辑（`filters.py`）：

```bash
python wbq.py fetch --mode snapshot --resume      # 抓取（等价 fetch_weibo.py）
python wbq.py stats                               # 库统计
python wbq.py photos --threshold 50 --limit 20    # 高清图筛选，可临时调阈值
python wbq.py memes --limit 50                    # N宗罪专题列表
python wbq.py interactions --limit 50             # 互动
python wbq.py search 垃圾桶 --limit 20 --json      # 关键词搜索 / JSON 导出
```

## 说明

- 反爬：走 weibo.com PC 端接口，只复用 Cookie、单线程串行、4–8s 抖动、每 30 页休眠、单日 500 次上限，命中 403/418/429/432 立即停止。详见 `PLAN.md`。
- Cookie 相当于账号密码，仅保存在本机 `.env`，勿外发、勿提交。

## 云端部署

采用 GitHub Actions 手动部署：本地把 SQLite 快照上传到私有 GitHub Release，
Actions 下载并校验快照后构建单镜像，再发布到 Fly.io。

```bash
./scripts/prepare-snapshot.sh   # 检查、压缩并上传快照
./deploy.sh                     # 触发 GitHub Actions
```

部署工作流只允许手动触发，不会在 `git push` 后自动发布。Cookie 和原始数据库不进入
Git，也不进入公网实例。默认关闭云端视频直链刷新，过期视频回退到原微博或视频原页。

首次账号准备、Fly 配置、验收与回滚见 `docs/deploy-sop.md`。
