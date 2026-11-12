# 评论接口探测记录（2026-11-10）

首轮离线与联网质量试跑结论另见
[`comment-pilot-2026-11-11.md`](comment-pilot-2026-11-11.md)。

## 可用的一级评论请求

接口：`https://weibo.com/ajax/statuses/buildComments`

参数：

```
is_reload=1
id=<微博 numeric id>
is_show_bulletin=2
is_mix=0
count=20
uid=<TARGET_UID>
flow=0
max_id=0
```

实测返回 200，`ok=1`，`data` 为一级评论列表，`max_id` 用于翻页。

## 已确认字段

- `like_counts`：评论点赞数，可做 top5 筛选。
- `isLikedByMblogAuthor`：博主是否点赞了这条评论。
- `comments`：内嵌的部分二级评论。
- `total_number`：该评论下的二级评论总数；当它大于 `comments` 长度时，说明还有未展开的回复。
- `idstr` / `rootidstr`：评论 id 与根评论 id。

## 尚未解决

“展开更多二级评论”的参数未确认。尝试过 `flow=1` 配合 `id`、`rootid`、`max_id` 的多种组合，均未返回目标回复；已停止继续猜参数，避免无效请求和风控风险。

下一步建议：在浏览器打开一条评论的“查看更多回复”，用开发者工具抓一次真实请求，按其参数复刻。不要在没有抓包依据时反复试错。

## 防误伤

`request_json` 已把 HTTP 400 改为立即停止，不再进入 60 秒自动重试。
