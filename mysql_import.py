"""把本地 SQLite 只读镜像导入微信云托管 MySQL。

默认只检查数据；传入 ``--apply`` 才会清空目标库中的小程序镜像表并重新导入。
数据库连接信息只从 MINIAPP_DB_* 环境变量读取，不写入命令行或日志。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id VARCHAR(32) PRIMARY KEY,
    mid VARCHAR(32),
    uid VARCHAR(32),
    screen_name VARCHAR(255),
    created_at VARCHAR(64),
    ts BIGINT,
    text LONGTEXT,
    text_plain LONGTEXT,
    reposts_count INT NOT NULL DEFAULT 0,
    comments_count INT NOT NULL DEFAULT 0,
    attitudes_count INT NOT NULL DEFAULT 0,
    source VARCHAR(255),
    is_longtext TINYINT NOT NULL DEFAULT 0,
    is_video TINYINT NOT NULL DEFAULT 0,
    is_live TINYINT NOT NULL DEFAULT 0,
    has_pics TINYINT NOT NULL DEFAULT 0,
    pics_json LONGTEXT,
    retweeted_user VARCHAR(255),
    raw_json LONGTEXT,
    fetched_at VARCHAR(64),
    INDEX idx_posts_ts (ts),
    INDEX idx_posts_hot (attitudes_count, reposts_count, comments_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memes (
    slug VARCHAR(80) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'behavior',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    aliases_json LONGTEXT NOT NULL,
    origin LONGTEXT NOT NULL,
    explanation LONGTEXT NOT NULL,
    behavior_tags_json LONGTEXT NOT NULL,
    exclude_json LONGTEXT NOT NULL,
    search_queries_json LONGTEXT NOT NULL,
    boundary LONGTEXT NOT NULL,
    representative_url LONGTEXT NOT NULL,
    representative_post_id VARCHAR(32),
    sort_order INT NOT NULL DEFAULT 0,
    updated_at VARCHAR(64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS post_memes (
    post_id VARCHAR(32) NOT NULL,
    meme_slug VARCHAR(80) NOT NULL,
    matched_by VARCHAR(32) NOT NULL,
    matched_term VARCHAR(255) NOT NULL,
    confidence DOUBLE NOT NULL DEFAULT 1,
    updated_at VARCHAR(64),
    PRIMARY KEY (post_id, meme_slug),
    INDEX idx_post_memes_slug (meme_slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS post_categories (
    post_id VARCHAR(32) NOT NULL,
    category VARCHAR(64) NOT NULL,
    matched_by VARCHAR(32) NOT NULL,
    matched_term VARCHAR(255) NOT NULL,
    classified_at VARCHAR(64),
    PRIMARY KEY (post_id, category),
    INDEX idx_post_categories_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS comments (
    id VARCHAR(32) PRIMARY KEY,
    post_id VARCHAR(32) NOT NULL,
    root_id VARCHAR(32) NOT NULL,
    parent_id VARCHAR(32) NOT NULL,
    user_name VARCHAR(255) NOT NULL,
    text_plain LONGTEXT NOT NULL,
    like_count INT NOT NULL DEFAULT 0,
    author_liked TINYINT NOT NULL DEFAULT 0,
    author_liked_known TINYINT NOT NULL DEFAULT 0,
    created_at VARCHAR(64) NOT NULL,
    is_reply TINYINT NOT NULL DEFAULT 0,
    reply_total INT NOT NULL DEFAULT 0,
    reply_saved INT NOT NULL DEFAULT 0,
    reply_complete TINYINT NOT NULL DEFAULT 0,
    fetched_at VARCHAR(64) NOT NULL,
    INDEX idx_comments_post (post_id),
    INDEX idx_comments_root (root_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS comment_root_selections (
    post_id VARCHAR(32) NOT NULL,
    comment_id VARCHAR(32) NOT NULL,
    page_rank INT NOT NULL,
    like_rank INT NOT NULL,
    selected_by_default TINYINT NOT NULL DEFAULT 0,
    selected_by_likes TINYINT NOT NULL DEFAULT 0,
    selection_order INT NOT NULL,
    fetched_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (post_id, comment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS comment_threads (
    post_id VARCHAR(32) PRIMARY KEY,
    fetched_at VARCHAR(64) NOT NULL,
    source VARCHAR(64) NOT NULL,
    roots_seen INT NOT NULL DEFAULT 0,
    roots_selected INT NOT NULL DEFAULT 0,
    replies_saved INT NOT NULL DEFAULT 0,
    max_id VARCHAR(64) NOT NULL,
    complete TINYINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS comment_pics (
    id VARCHAR(80) PRIMARY KEY,
    post_id VARCHAR(32),
    comment_id VARCHAR(32),
    thumb LONGTEXT,
    url LONGTEXT,
    author VARCHAR(255),
    fetched_at VARCHAR(64),
    INDEX idx_comment_pics_post (post_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS miniapp_meta (
    k VARCHAR(80) PRIMARY KEY,
    v LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

TABLES: dict[str, tuple[str, ...]] = {
    "posts": (
        "id",
        "mid",
        "uid",
        "screen_name",
        "created_at",
        "ts",
        "text",
        "text_plain",
        "reposts_count",
        "comments_count",
        "attitudes_count",
        "source",
        "is_longtext",
        "is_video",
        "is_live",
        "has_pics",
        "pics_json",
        "retweeted_user",
        "raw_json",
        "fetched_at",
    ),
    "memes": (
        "slug",
        "name",
        "kind",
        "status",
        "aliases_json",
        "origin",
        "explanation",
        "behavior_tags_json",
        "exclude_json",
        "search_queries_json",
        "boundary",
        "representative_url",
        "representative_post_id",
        "sort_order",
        "updated_at",
    ),
    "post_memes": (
        "post_id",
        "meme_slug",
        "matched_by",
        "matched_term",
        "confidence",
        "updated_at",
    ),
    "post_categories": (
        "post_id",
        "category",
        "matched_by",
        "matched_term",
        "classified_at",
    ),
    "comments": (
        "id",
        "post_id",
        "root_id",
        "parent_id",
        "user_name",
        "text_plain",
        "like_count",
        "author_liked",
        "author_liked_known",
        "created_at",
        "is_reply",
        "reply_total",
        "reply_saved",
        "reply_complete",
        "fetched_at",
    ),
    "comment_root_selections": (
        "post_id",
        "comment_id",
        "page_rank",
        "like_rank",
        "selected_by_default",
        "selected_by_likes",
        "selection_order",
        "fetched_at",
    ),
    "comment_threads": (
        "post_id",
        "fetched_at",
        "source",
        "roots_seen",
        "roots_selected",
        "replies_saved",
        "max_id",
        "complete",
    ),
    "comment_pics": (
        "id",
        "post_id",
        "comment_id",
        "thumb",
        "url",
        "author",
        "fetched_at",
    ),
}


def _chunks(rows: Iterable[tuple], size: int) -> Iterator[list[tuple]]:
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def inspect_sqlite(path: Path) -> dict[str, int]:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite 快照不存在：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check 失败：{integrity}")
        counts = {}
        for table in TABLES:
            counts[table] = int(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
        if counts["posts"] < 1:
            raise ValueError("SQLite 快照没有微博")
        return counts
    finally:
        conn.close()


def _mysql_connect():
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise RuntimeError("导入 MySQL 需要安装 PyMySQL") from exc
    password = os.environ.get("MINIAPP_DB_PASSWORD", "")
    if not password:
        raise RuntimeError("缺少 MINIAPP_DB_PASSWORD")
    return pymysql.connect(
        host=os.environ.get("MINIAPP_DB_HOST", ""),
        port=int(os.environ.get("MINIAPP_DB_PORT", "3306")),
        user=os.environ.get("MINIAPP_DB_USER", ""),
        password=password,
        database=os.environ.get("MINIAPP_DB_NAME", ""),
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


def import_sqlite(
    source: Path,
    *,
    apply: bool,
    batch_size: int,
    snapshot_label: str,
) -> dict:
    expected = inspect_sqlite(source)
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("batch-size 必须在 1 到 1000 之间")
    if not apply:
        return {"mode": "dry-run", "counts": expected}

    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target = _mysql_connect()
    imported: dict[str, int] = {}
    try:
        with target.cursor() as cursor:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    cursor.execute(statement)
        target.commit()

        with target.cursor() as cursor:
            for table in reversed(tuple(TABLES)):
                cursor.execute(f"DELETE FROM {table}")
            for table, columns in TABLES.items():
                selected = ", ".join(columns)
                source_rows = source_conn.execute(
                    f"SELECT {selected} FROM {table}"
                )
                insert_sql = (
                    f"INSERT INTO {table} ({selected}) VALUES "
                    f"({', '.join(['%s'] * len(columns))})"
                )
                imported[table] = 0
                for batch in _chunks(source_rows, batch_size):
                    cursor.executemany(insert_sql, batch)
                    imported[table] += len(batch)
            imported_at = datetime.now(timezone.utc).isoformat()
            metadata = {
                "snapshot_label": snapshot_label or source.name,
                "imported_at": imported_at,
                "post_count": str(expected["posts"]),
            }
            cursor.executemany(
                "INSERT INTO miniapp_meta (k, v) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE v = VALUES(v)",
                list(metadata.items()),
            )
        target.commit()

        with target.cursor() as cursor:
            actual: dict[str, int] = {}
            for table in TABLES:
                cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                actual[table] = int(cursor.fetchone()["count"])
        if actual != expected:
            raise RuntimeError(
                f"导入后数量不一致，期望 {expected}，实际 {actual}"
            )
        return {"mode": "apply", "counts": actual}
    except Exception:
        target.rollback()
        raise
    finally:
        source_conn.close()
        target.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--snapshot-label", default="")
    args = parser.parse_args(argv)
    try:
        result = import_sqlite(
            args.sqlite,
            apply=args.apply,
            batch_size=args.batch_size,
            snapshot_label=args.snapshot_label,
        )
    except Exception as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
