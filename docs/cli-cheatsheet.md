# wbq.py 速查表

所有命令都在项目根目录 `code/weibo-beagle-qianqian/` 下执行，统一用 venv 里的 Python：

```bash
cd /Users/kerryyu/Documents/Kerry-TechWiki/code/weibo-beagle-qianqian
export PYBIN=.venv/bin/python
```

## 抓取（唯一联网写入命令）

```bash
$PYBIN wbq.py fetch --mode probe                # 校验 Cookie 是否有效，打印首条样本
$PYBIN wbq.py fetch --mode snapshot             # 全量快照，从第 1 页开始
$PYBIN wbq.py fetch --mode snapshot --resume    # 从检查点续抓（日限 500 次触发后，下轮用它继续）
$PYBIN wbq.py fetch --mode incremental          # 增量：自上次检查点起补新
$PYBIN wbq.py fetch --mode snapshot --limit 5   # 只抓 5 页，测试用
```

退出码：`0` 正常，`2` Cookie 失效，`3` 被风控/日限，`130` 手动中断（检查点已保存）。
每次 fetch 结束（含日限中断）后会自动运行一次分类整理。

## 查看与筛选（纯读库，不联网）

```bash
$PYBIN wbq.py stats                              # 库统计：总数/含图/视频/live/转发
$PYBIN wbq.py request-status                     # 今日共享微博请求额度
$PYBIN wbq.py request-status --day 2026-11-11    # 指定日期额度
$PYBIN wbq.py clean-pics                         # 清理空图片项，修复因空 URL 产生的裂图
$PYBIN wbq.py repair-media                       # 修复 mix_media_info 混合媒体的图片与视频
$PYBIN wbq.py videos-warm --limit 30             # 预热 30 条视频直链，让首播更快
$PYBIN wbq.py classify                           # 整理所有尚未分类的新微博
$PYBIN wbq.py classify --day 2026-11-10          # 只整理指定入库日期
$PYBIN wbq.py classify --all                     # 重新整理全部微博
$PYBIN wbq.py memes                              # 列出N宗罪专题及各自关联条数
$PYBIN wbq.py meme chishi                        # 查看某个N宗罪专题下的微博（按时间正序）
$PYBIN wbq.py meme chishi --order hot --limit 20  # 按热度查看某个N宗罪专题
$PYBIN wbq.py memes-raw                          # 旧版关键词罪证列表（dsnmd/吃屎/垃圾桶/抢零食）
$PYBIN wbq.py interactions                       # 与 @比格犬受害者联盟 的互动
$PYBIN wbq.py photos                              # 高清图（默认阈值见 config.PHOTO_MIN_ENGAGEMENT）
$PYBIN wbq.py photos --threshold 100              # 自定义阈值：转+评 > 100
$PYBIN wbq.py search 垃圾桶                       # 关键词检索（空格分词，AND 关系）
```

通用 flags：`--limit N` 控制条数，`--json` 输出机器可读 JSON。

多个 Git worktree 并行联网时，用同一个绝对路径设置中央请求账本：

```bash
export REQUEST_LEDGER_DB=/path/to/shared/request-ledger.db
$PYBIN wbq.py request-status
```

语料数据库可以通过 `DB_PATH` 各自隔离，但 `REQUEST_LEDGER_DB` 必须共享。

## N宗罪条目维护（人工运营）

N宗罪条目存放在 `memes.seed.toml`。每个条目可以是一项热梗，也可以是一类行为主题。新增条目：复制一段 `[[memes]]`，填写 `name`、`aliases`、`behavior_tags`、`exclude`、`search_queries` 等字段，然后执行：

```bash
$PYBIN wbq.py memes-import                       # 导入种子并按触发词重建关联
$PYBIN wbq.py baseline build --version baseline-v1
                                                  # 只用本地库构建并冻结 baseline
$PYBIN wbq.py baseline probe --output data/search-probe-v2.json
                                                  # 每个唯一搜索词只取 1 页；消耗共享请求额度
$PYBIN wbq.py baseline build --version baseline-v2 \
    --search-evidence data/search-probe-v2.json
                                                  # 离线消费完整 probe 证据并冻结 v2
$PYBIN wbq.py baseline report --version baseline-v1
                                                  # 查看公开/候选来源及查询排名统计
$PYBIN wbq.py baseline review --version baseline-v2 --output dist/baseline-v2-review
                                                  # 导出 JSON/CSV/Markdown 人工审核清单
```

`slug` 用于专题页 URL（`/meme/<slug>`）；不填时默认使用 `name`。代表微博的 `representative_url` 尚未入库时会显示“尚未入库”，抓取到对应 mid 后重新导入即可自动关联。

搜索 probe 先检查 `request-status`，并让 `REQUEST_LEDGER_DB` 指向共享账本。probe 只记录完整结果和一页排名；风控、Cookie 失效、400 或额度耗尽会立即停止。未完成的证据可用于排查，但不能构建 baseline。`baseline build --search-pages 1` 仍保留为一体化入口，但推荐的审计流程是先 probe、保存证据，再离线 build。

## 评论图片（骨架，待全量入库后启用）

```bash
$PYBIN wbq.py comments                            # 列出已入库的评论图片
$PYBIN wbq.py comments --fetch <微博id> --pages 3 # 抓取某条微博下评论里的图片
```

## 评论质量试跑

```bash
$PYBIN wbq.py comments-pilot
    # 离线选择 20 条 N宗罪/互动分层样本，不联网
$PYBIN wbq.py comments-pilot --fetch --output dist/comment-pilot
    # 每帖抓一级评论首页，并导出 JSON/Markdown 审核包
```

评论试跑最多 20 条；每条只有一次一级评论首页请求，网络错误、400、风控和额度
上限均立即停止。已完成的 pilot 微博会在重跑时跳过，避免重复消耗额度。

评论请求共享 `REQUEST_LEDGER_DB` 的每日额度。审核包记录评论的首页位次、点赞
位次和入选原因，可复核默认前 10 与点赞前 5 的并集。二级回复只保存接口内嵌
部分，并分别标记根评论回复是否保存完整、整条微博首页是否翻完。

## 跑站点

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

页面：`/` 热门、`/memes` N宗罪、`/meme/{slug}` 单个罪名专题、`/memes/all` 全部罪证、`/interactions` 互动（源微博图片与视频）、`/photos` 高清图、`/search?q=` 搜索、`/post/{id}` 详情。所有列表卡片中的视频均可直接播放。

## 改筛选口径

只改一处真相源：`filters.py`（Web 与 CLI 共用）。关键词与阈值在 `config.py`（或 `.env` 覆盖）。

> 反爬红线：不要调低 `RATE_MIN_SEC/RATE_MAX_SEC`，不要绕过 `DAILY_MAX_REQUESTS=500`，遇到 `[Cookie]`/`[停止]` 就停，不要短间隔重试。
