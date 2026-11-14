#!/usr/bin/env python3
"""比格钱钱微博 · 数据筛选管线 CLI。

把「抓取 → 入库 → 筛选/导出」串成一条可复用命令行。

用法：
  python wbq.py fetch --mode snapshot [--resume] [--limit N]
  python wbq.py stats
  python wbq.py photos [--threshold N] [--limit N] [--json]
  python wbq.py memes [--limit N] [--json]
  python wbq.py interactions [--limit N] [--json]
  python wbq.py search <关键词> [--limit N] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import filters
import store

CST = timezone(timedelta(hours=8))


def _engagement(r: dict) -> int:
    return int(r["attitudes_count"]) + 5 * int(r["reposts_count"]) + 3 * int(r["comments_count"])


def _fmt_ts(ts) -> str:
    if not ts:
        return "        "
    try:
        return datetime.fromtimestamp(int(ts), tz=CST).strftime("%Y-%m-%d")
    except Exception:
        return "        "


def _emit(rows, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        flags = ("图" if r["has_pics"] else " ") + ("视" if r["is_video"] else " ") + ("L" if r["is_live"] else " ")
        print(f"{_fmt_ts(r['ts'])}  {r['id']}  赞{r['attitudes_count']} 转{r['reposts_count']} 评{r['comments_count']}  [{flags}]  {r['text_plain'][:48]}")


def cmd_fetch(args) -> int:
    from fetch_weibo import run as fetch_run
    from fetch_weibo import StageBudgetExhausted, WeiboAuthError, WeiboBlocked

    rc = 0
    try:
        fetch_run(args.mode, args.resume, args.limit, args.max_requests)
    except StageBudgetExhausted as exc:
        print(f"[阶段预算] {exc}", file=sys.stderr)
        rc = 4
    except WeiboAuthError as exc:
        print(f"[Cookie] {exc}", file=sys.stderr)
        rc = 2
    except WeiboBlocked as exc:
        print(f"[停止] {exc}", file=sys.stderr)
        rc = 3
    except KeyboardInterrupt:
        print("\n已中断，检查点已保存，可用 --resume 继续。", file=sys.stderr)
        rc = 130
    finally:
        try:
            import memes
            import classify

            memes.sync()
            result = classify.run()
            print(
                "已整理新入库微博："
                f"{result['processed']} 条（热门 {result['hot']}、N宗罪 {result['memes']}、"
                f"互动 {result['interactions']}、萌钱CP {result['interaction_mengqian_cp']}、"
                f"实体别名 {result['qianqian_aliases']}、高清图 {result['photos']}）。"
            )
        except Exception as exc:
            print(f"[分类] 失败：{exc}", file=sys.stderr)
    return rc


def _query(where, params, args) -> int:
    store.init_db()
    rows = store.query_posts(where, params, limit=args.limit, order="hot")
    print(f"命中 {len(rows)} 条：", file=sys.stderr)
    _emit(rows, args.json)
    return 0


def cmd_photos(args) -> int:
    where, params = filters.photo_where(args.threshold)
    return _query(where, params, args)


def cmd_memes_raw(args) -> int:
    where, params = filters.meme_where()
    return _query(where, params, args)


def cmd_memes_import(args) -> int:
    import memes

    try:
        summary = memes.sync(args.seed) if args.seed else memes.sync()
    except (OSError, ValueError) as exc:
        print(f"[导入失败] {exc}", file=sys.stderr)
        return 1
    for item in summary:
        terms = "、".join(item["term_counts"]) or "无"
        if item["match_mode"] == "curated":
            basis = "人工审定"
        elif item["match_mode"] == "mixed":
            basis = f"人工审定 + 自动公开词：{terms}"
        else:
            basis = terms
        print(
            f"{item['name']}（{item['slug']}）：命中 {item['matched_posts']} 条；"
            f"依据 {basis}；代表微博{'已关联' if item['representative_found'] else '尚未入库'}"
        )
    import classify

    result = classify.run(all_posts=True)
    print(f"已按新N宗罪条目重新整理 {result['processed']} 条微博。")
    return 0


def cmd_meme_list(args) -> int:
    store.init_db()
    rows = store.list_memes()
    if args.json:
        for row in rows:
            row["aliases"] = json.loads(row.pop("aliases_json") or "[]")
            row["behavior_tags"] = json.loads(row.pop("behavior_tags_json") or "[]")
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("暂无N宗罪条目，请运行 wbq.py memes-import 导入种子。", file=sys.stderr)
        return 0
    for row in rows:
        print(f"{row['slug']:<16} {row['name']:<10} 关联 {row['post_count']:>4} 条  {row['explanation'][:40]}")
    return 0


def cmd_meme(args) -> int:
    store.init_db()
    meme = store.get_meme(args.slug)
    if not meme:
        print(f"未找到N宗罪条目：{args.slug}", file=sys.stderr)
        return 1
    if not args.json:
        print(f"【{meme['name']}】{meme['explanation']}", file=sys.stderr)
        if meme["origin"]:
            print(f"黑话来源：{meme['origin']}", file=sys.stderr)
        if meme["boundary"]:
            print(f"判断边界：{meme['boundary']}", file=sys.stderr)
    rows = store.query_meme_posts(args.slug, limit=args.limit, order=args.order)
    print(f"命中 {len(rows)} 条：", file=sys.stderr)
    _emit(rows, args.json)
    return 0


def cmd_interactions(args) -> int:
    where, params = filters.interact_where()
    return _query(where, params, args)


def cmd_search(args) -> int:
    where, params = filters.search_where(args.query)
    return _query(where, params, args)


def cmd_comments(args) -> int:
    store.init_db()
    if args.fetch:
        from fetch_weibo import RateLimiter, WeiboAuthError, WeiboBlocked
        from fetch_weibo import fetch_comments, make_session, pics_from_comment

        session = make_session()
        limiter = RateLimiter()
        max_id = 0
        saved = 0
        for _ in range(args.pages):
            try:
                items, new_max = fetch_comments(session, limiter, args.fetch, max_id=max_id)
            except (WeiboAuthError, WeiboBlocked) as exc:
                print(f"[停止] {exc}", file=sys.stderr)
                return 2
            if not items:
                break
            for c in items:
                for pic in pics_from_comment(c):
                    author = (c.get("user") or {}).get("screen_name") or ""
                    store.upsert_comment_pic({
                        "id": f"{args.fetch}_{c.get('id')}_{pic['pid']}",
                        "post_id": args.fetch,
                        "comment_id": str(c.get("id") or ""),
                        "thumb": pic["thumb"],
                        "url": pic["url"],
                        "author": author,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    })
                    saved += 1
            if not new_max or str(new_max) == str(max_id):
                break
            max_id = new_max
        print(f"已入库 {saved} 张评论图。", flush=True)
        return 0

    rows = store.query_comment_pics(limit=args.limit)
    print(f"命中 {len(rows)} 张：", file=sys.stderr)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['post_id']}  @{r['author']}  {r['thumb'][:72]}")
    return 0


def cmd_comments_pilot(args) -> int:
    import comment_pilot
    from fetch_weibo import WeiboAuthError, WeiboBlocked

    store.init_db()
    posts = comment_pilot.select_posts(
        limit=args.limit,
        strategy=args.strategy,
        candidate_count=args.candidate_count,
    )
    exit_code = 0
    result = {
        "selection_strategy": args.strategy,
        "selected_posts": len(posts),
        "posts": [
            {
                "post_id": post["id"],
                "reason": post["selection_reason"],
                "comments_count": post["comments_count"],
                "text": post.get("text_plain") or "",
            }
            for post in posts
        ],
    }
    if args.fetch:
        try:
            result["fetch"] = comment_pilot.fetch(posts)
        except (WeiboAuthError, WeiboBlocked) as exc:
            print(f"[停止] {exc}", file=sys.stderr)
            exit_code = 2
    if args.output:
        result["export"] = comment_pilot.export(
            posts,
            Path(args.output),
            strategy=args.strategy,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


def cmd_stats(args) -> int:
    store.init_db()
    conn = store.connect()
    try:
        def count(sql: str, params: tuple = ()) -> int:
            return conn.execute(sql, params).fetchone()[0]

        n_repost = count("select count(*) from posts where retweeted_user != ''")
        print(f"总数: {store.count_posts()}")
        print(f"含图: {count('select count(*) from posts where has_pics=1')}")
        print(f"视频: {count('select count(*) from posts where is_video=1')}")
        print(f"live: {count('select count(*) from posts where is_live=1')}")
        print(f"转发: {n_repost}")
    finally:
        conn.close()
    return 0


def cmd_request_status(args) -> int:
    store.init_db()
    day = args.day or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    stats = store.weibo_request_stats(day)
    remaining = max(0, config.DAILY_MAX_REQUESTS - stats["total"])
    print(f"账本：{config.LEDGER_DB_PATH}")
    print(f"{day} 已用 {stats['total']}/{config.DAILY_MAX_REQUESTS}，剩余 {remaining}")
    for category, count in stats["categories"].items():
        print(f"{category}: {count}")
    return 0


def cmd_baseline(args) -> int:
    import baseline
    from fetch_weibo import WeiboAuthError, WeiboBlocked

    try:
        if args.baseline_cmd == "build":
            result = baseline.build(
                args.version,
                search_pages=args.search_pages,
                search_evidence=args.search_evidence,
            )
        elif args.baseline_cmd == "probe":
            result = baseline.probe_search(
                label=args.label or args.output,
                output_path=args.output,
            )
        elif args.baseline_cmd == "review":
            result = baseline.export_review(args.version, args.output)
        else:
            result = baseline.report(args.version)
    except WeiboAuthError as exc:
        print(f"[Cookie] {exc}", file=sys.stderr)
        return 2
    except WeiboBlocked as exc:
        print(f"[停止] {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"[baseline] 失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_clean_pics(args) -> int:
    store.init_db()
    result = store.clean_empty_pics()
    print(f"已清理 {result['changed']} 条记录，其中 {result['emptied']} 条不再含有效图片。")
    return 0


def cmd_repair_media(args) -> int:
    import fetch_weibo

    store.init_db()
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT id, is_video, is_live, has_pics, pics_json, raw_json "
            "FROM posts WHERE raw_json LIKE '%mix_media_info%'"
        ).fetchall()
    finally:
        conn.close()
    changed = 0
    for row in rows:
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except ValueError:
            continue
        pics = fetch_weibo.pics_from_pic_infos(raw)
        if not pics:
            continue
        is_video = 1 if fetch_weibo.is_video(raw) else int(row["is_video"] or 0)
        is_live = 1 if fetch_weibo.is_live(raw) else int(row["is_live"] or 0)
        pics_json = json.dumps(pics, ensure_ascii=False)
        if (
            is_video != int(row["is_video"] or 0)
            or is_live != int(row["is_live"] or 0)
            or int(row["has_pics"] or 0) != 1
            or pics_json != (row["pics_json"] or "[]")
        ):
            store.update_post_media(row["id"], is_video, is_live, 1, pics_json)
            changed += 1
    print(f"已修复 {changed} 条混合媒体微博。")
    import classify

    result = classify.run(all_posts=True)
    print(f"已重新整理 {result['processed']} 条微博。")
    return 0


def cmd_videos_warm(args) -> int:
    from app.main import resolve_video, video_info

    store.init_db()
    where = "is_video = 1 OR (raw_json LIKE '%retweeted_status%' AND raw_json LIKE '%stream_url%')"
    rows = store.query_posts(where, (), limit=args.limit, order=args.order)
    warmed = 0
    for p in rows:
        info = video_info(p)
        if not info:
            continue
        resolved = resolve_video(p, info["source"] == 1)
        print(f"{p['id']} source={info['source']} {'ok' if resolved else 'failed'}")
        warmed += 1 if resolved else 0
    print(f"预热完成：成功 {warmed}/{len(rows)}。")
    return 0 if warmed else 1


def cmd_classify(args) -> int:
    import classify

    result = classify.run(day=args.day, all_posts=args.all)
    print(
        f"已整理 {result['processed']} 条：热门 {result['hot']}、N宗罪 {result['memes']}、"
        f"互动 {result['interactions']}、萌钱CP {result['interaction_mengqian_cp']}、"
        f"实体别名 {result['qianqian_aliases']}、高清图 {result['photos']}。"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wbq", description="比格钱钱微博 · 数据筛选管线 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="抓取微博入库")
    f.add_argument(
        "--mode",
        choices=["probe", "snapshot", "incremental", "auto"],
        default="snapshot",
    )
    f.add_argument("--resume", action="store_true")
    f.add_argument("--limit", type=int, default=None)
    f.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="本次最多消耗的请求数，不影响共享单日上限",
    )

    sub.add_parser("stats", help="库统计")

    rs = sub.add_parser("request-status", help="查看共享微博请求额度")
    rs.add_argument("--day", default=None, help="日期 YYYY-MM-DD，默认今天（上海时区）")

    bl = sub.add_parser("baseline", help="构建或查看 N宗罪 baseline")
    bls = bl.add_subparsers(dest="baseline_cmd", required=True)
    blb = bls.add_parser("build", help="构建并冻结 baseline")
    blb.add_argument("--version", required=True, help="不可变 baseline 版本")
    blb.add_argument(
        "--search-pages",
        type=int,
        choices=(0, 1),
        default=0,
        help="受控联网 probe 仅允许 1 页；0 表示离线构建",
    )
    blb.add_argument(
        "--search-evidence",
        default=None,
        help="读取已保存的一页 probe 证据离线构建",
    )
    blr = bls.add_parser("report", help="查看 baseline 报告")
    blr.add_argument("--version", required=True, help="baseline 版本")
    blp = bls.add_parser("probe", help="对每个唯一搜索词执行一次一页 probe")
    blp.add_argument("--output", required=True, help="证据 JSON 输出路径")
    blp.add_argument("--label", default=None, help="证据标签")
    blv = bls.add_parser("review", help="导出人工审核清单")
    blv.add_argument("--version", required=True)
    blv.add_argument("--output", required=True, help="审核清单输出目录")

    sub.add_parser("clean-pics", help="清理 pics_json 中 thumb/url 都为空的图片项")

    sub.add_parser("repair-media", help="修复 mix_media_info 混合媒体的图片与视频标记")

    vw = sub.add_parser("videos-warm", help="预先刷新视频直链并写入缓存")
    vw.add_argument("--limit", type=int, default=30)
    vw.add_argument("--order", choices=["hot", "new"], default="hot")

    cf = sub.add_parser("classify", help="按 tab 整理新入库微博")
    cf.add_argument("--day", default=None, help="按入库日期整理，格式 YYYY-MM-DD")
    cf.add_argument("--all", action="store_true", help="重新整理全部微博")

    c = sub.add_parser("comments", help="评论图片（骨架）")
    c.add_argument("--fetch", type=str, default=None, help="抓取指定微博 id 的评论图片")
    c.add_argument("--pages", type=int, default=3, help="翻页数，默认 3")
    c.add_argument("--limit", type=int, default=100)
    c.add_argument("--json", action="store_true")

    cp = sub.add_parser("comments-pilot", help="20条评论质量试跑")
    cp.add_argument(
        "--limit",
        type=int,
        choices=range(1, 21),
        default=20,
        help="样本数，最多 20",
    )
    cp.add_argument("--fetch", action="store_true", help="联网抓取；默认只做离线选样")
    cp.add_argument("--output", default=None, help="导出审核包的目录")
    cp.add_argument(
        "--strategy",
        choices=("legacy", "candidate-first"),
        default="legacy",
        help="选帖模式；默认保持现有流程",
    )
    cp.add_argument(
        "--candidate-count",
        type=int,
        default=12,
        help="候选优先模式下用于待审候选的帖子数",
    )

    s = sub.add_parser("photos", help="高清图筛选")
    s.add_argument("--threshold", type=int, default=None, help="转评总数阈值，默认取 config.PHOTO_MIN_ENGAGEMENT")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--json", action="store_true")

    for name, help_ in [("memes-raw", "旧版关键词罪证列表"), ("interactions", "互动")]:
        s = sub.add_parser(name, help=help_)
        s.add_argument("--limit", type=int, default=100)
        s.add_argument("--json", action="store_true")

    mi = sub.add_parser("memes-import", help="导入N宗罪条目种子并重建规则关联")
    mi.add_argument("--seed", default=None, help="种子文件路径，默认 memes.seed.toml")

    ml = sub.add_parser("memes", help="列出N宗罪专题")
    ml.add_argument("--json", action="store_true")

    mm = sub.add_parser("meme", help="查看单个N宗罪专题的微博")
    mm.add_argument("slug")
    mm.add_argument("--limit", type=int, default=200)
    mm.add_argument(
        "--order",
        choices=["earliest", "latest", "hot", "likes", "new"],
        default="earliest",
    )
    mm.add_argument("--json", action="store_true")

    s = sub.add_parser("search", help="关键词搜索")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--json", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "fetch": cmd_fetch,
        "stats": cmd_stats,
        "request-status": cmd_request_status,
        "baseline": cmd_baseline,
        "clean-pics": cmd_clean_pics,
        "repair-media": cmd_repair_media,
        "videos-warm": cmd_videos_warm,
        "classify": cmd_classify,
        "photos": cmd_photos,
        "memes": cmd_meme_list,
        "memes-raw": cmd_memes_raw,
        "memes-import": cmd_memes_import,
        "meme": cmd_meme,
        "interactions": cmd_interactions,
        "comments": cmd_comments,
        "comments-pilot": cmd_comments_pilot,
        "search": cmd_search,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
