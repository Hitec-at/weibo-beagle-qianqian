"""SQLite 存储与查询。"""
from __future__ import annotations

import json
import sqlite3

from config import DB_PATH, LEDGER_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    mid TEXT,
    uid TEXT,
    screen_name TEXT,
    created_at TEXT,
    ts INTEGER,
    text TEXT,
    text_plain TEXT,
    reposts_count INTEGER DEFAULT 0,
    comments_count INTEGER DEFAULT 0,
    attitudes_count INTEGER DEFAULT 0,
    source TEXT,
    is_longtext INTEGER DEFAULT 0,
    is_video INTEGER DEFAULT 0,
    is_live INTEGER DEFAULT 0,
    has_pics INTEGER DEFAULT 0,
    pics_json TEXT,
    retweeted_user TEXT,
    raw_json TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(uid);
CREATE INDEX IF NOT EXISTS idx_posts_ts ON posts(ts);
CREATE INDEX IF NOT EXISTS idx_posts_attitudes ON posts(attitudes_count);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS comment_pics (
    id TEXT PRIMARY KEY,
    post_id TEXT,
    comment_id TEXT,
    thumb TEXT,
    url TEXT,
    author TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_comment_pics_post ON comment_pics(post_id);
CREATE TABLE IF NOT EXISTS memes (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    behavior_tags_json TEXT NOT NULL DEFAULT '[]',
    exclude_json TEXT NOT NULL DEFAULT '[]',
    search_queries_json TEXT NOT NULL DEFAULT '[]',
    boundary TEXT NOT NULL DEFAULT '',
    representative_url TEXT NOT NULL DEFAULT '',
    representative_post_id TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS post_memes (
    post_id TEXT NOT NULL,
    meme_slug TEXT NOT NULL,
    matched_by TEXT NOT NULL DEFAULT 'rule',
    matched_term TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    updated_at TEXT,
    PRIMARY KEY (post_id, meme_slug)
);
CREATE INDEX IF NOT EXISTS idx_post_memes_meme ON post_memes(meme_slug);
CREATE TABLE IF NOT EXISTS video_cache (
    key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    poster TEXT,
    expires_at INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS weibo_request_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_day TEXT NOT NULL,
    category TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    requested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weibo_request_day ON weibo_request_ledger(request_day);
CREATE TABLE IF NOT EXISTS post_categories (
    post_id TEXT NOT NULL,
    category TEXT NOT NULL,
    matched_by TEXT NOT NULL DEFAULT 'rule',
    matched_term TEXT NOT NULL DEFAULT '',
    classified_at TEXT,
    PRIMARY KEY (post_id, category)
);
CREATE INDEX IF NOT EXISTS idx_post_categories_category ON post_categories(category);
"""

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS weibo_request_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_day TEXT NOT NULL,
    category TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    requested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weibo_request_day ON weibo_request_ledger(request_day);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def connect_ledger() -> sqlite3.Connection:
    LEDGER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LEDGER_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memes)")}
        if "exclude_json" not in columns:
            conn.execute(
                "ALTER TABLE memes ADD COLUMN exclude_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "search_queries_json" not in columns:
            conn.execute(
                "ALTER TABLE memes ADD COLUMN search_queries_json TEXT NOT NULL DEFAULT '[]'"
            )
        conn.commit()
    finally:
        conn.close()
    ledger = connect_ledger()
    try:
        ledger.executescript(LEDGER_SCHEMA)
        if LEDGER_DB_PATH != DB_PATH:
            existing = ledger.execute(
                "SELECT COUNT(*) FROM weibo_request_ledger"
            ).fetchone()[0]
            if existing == 0:
                corpus = connect()
                try:
                    rows = corpus.execute(
                        """
                        SELECT request_day, category, endpoint, requested_at
                        FROM weibo_request_ledger
                        """
                    ).fetchall()
                finally:
                    corpus.close()
                if rows:
                    ledger.executemany(
                        """
                        INSERT INTO weibo_request_ledger
                            (request_day, category, endpoint, requested_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            (
                                row["request_day"],
                                row["category"],
                                row["endpoint"],
                                row["requested_at"],
                            )
                            for row in rows
                        ],
                    )
        ledger.commit()
    finally:
        ledger.close()


def get_meta(key: str, default: str | None = None) -> str | None:
    conn = connect()
    try:
        row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else default
    finally:
        conn.close()


def set_meta(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO meta(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_post(p: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO posts (
                id, mid, uid, screen_name, created_at, ts, text, text_plain,
                reposts_count, comments_count, attitudes_count, source,
                is_longtext, is_video, is_live, has_pics, pics_json,
                retweeted_user, raw_json, fetched_at
            ) VALUES (
                :id, :mid, :uid, :screen_name, :created_at, :ts, :text, :text_plain,
                :reposts_count, :comments_count, :attitudes_count, :source,
                :is_longtext, :is_video, :is_live, :has_pics, :pics_json,
                :retweeted_user, :raw_json, :fetched_at
            )
            ON CONFLICT(id) DO UPDATE SET
                mid = excluded.mid,
                text = excluded.text,
                text_plain = excluded.text_plain,
                reposts_count = excluded.reposts_count,
                comments_count = excluded.comments_count,
                attitudes_count = excluded.attitudes_count,
                is_video = excluded.is_video,
                is_live = excluded.is_live,
                has_pics = excluded.has_pics,
                pics_json = excluded.pics_json,
                retweeted_user = excluded.retweeted_user,
                raw_json = excluded.raw_json,
                fetched_at = excluded.fetched_at
            """,
            p,
        )
        conn.commit()
    finally:
        conn.close()


def engagement_sql() -> str:
    return "(attitudes_count + 5 * reposts_count + 3 * comments_count)"


def count_posts(where: str = "1=1", params: tuple = ()) -> int:
    conn = connect()
    try:
        return conn.execute(
            f"SELECT COUNT(*) AS c FROM posts WHERE {where}", params
        ).fetchone()["c"]
    finally:
        conn.close()


def query_posts(
    where: str,
    params: tuple = (),
    limit: int = 100,
    order: str = "hot",
    offset: int = 0,
) -> list[dict]:
    orders = {
        "hot": f"{engagement_sql()} DESC, ts DESC, id DESC",
        "latest": "ts DESC, id DESC",
        "new": "ts DESC, id DESC",
        "earliest": "ts ASC, id ASC",
        "likes": "attitudes_count DESC, ts DESC, id DESC",
    }
    clause = orders.get(order, orders["hot"])
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM posts WHERE {where} ORDER BY {clause} LIMIT ? OFFSET ?",
            (*params, limit, max(0, offset)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_relevance_sql(terms: list[str]) -> tuple[str, tuple[str, ...]]:
    """搜索排序：完整短语、词频、首次命中位置，再按热度破同分。"""
    terms = [term for term in terms if term]
    if not terms:
        return f"{engagement_sql()} DESC, ts DESC, id DESC", ()
    parts: list[str] = []
    params: list[str] = []
    phrase = " ".join(terms)
    if phrase:
        parts.append(
            "(CASE WHEN instr(lower(text_plain), lower(?)) > 0 "
            "THEN 2000 + max(0, 500 - instr(lower(text_plain), lower(?))) ELSE 0 END)"
        )
        params.extend([phrase, phrase])
    for term in terms:
        parts.append(
            "(CASE WHEN instr(lower(text_plain), lower(?)) > 0 "
            "THEN max(0, 400 - instr(lower(text_plain), lower(?))) ELSE 0 END)"
        )
        params.extend([term, term])
        parts.append(
            "(((length(lower(text_plain)) - "
            "length(replace(lower(text_plain), lower(?), ''))) / max(length(?), 1)) * 20)"
        )
        params.extend([term, term])
    score = " + ".join(parts)
    return f"({score}) DESC, {engagement_sql()} DESC, ts DESC, id DESC", tuple(params)


def query_posts_relevance(
    where: str,
    params: tuple,
    terms: list[str],
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    clause, order_params = search_relevance_sql(terms)
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM posts WHERE {where} ORDER BY {clause} LIMIT ? OFFSET ?",
            (*params, *order_params, limit, max(0, offset)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_post(pid: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_post_media(
    pid: str, is_video: int, is_live: int, has_pics: int, pics_json: str
) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            UPDATE posts
            SET is_video = ?, is_live = ?, has_pics = ?, pics_json = ?
            WHERE id = ?
            """,
            (is_video, is_live, has_pics, pics_json, pid),
        )
        conn.commit()
    finally:
        conn.close()

def upsert_comment_pic(p: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO comment_pics (id, post_id, comment_id, thumb, url, author, fetched_at)
            VALUES (:id, :post_id, :comment_id, :thumb, :url, :author, :fetched_at)
            ON CONFLICT(id) DO UPDATE SET
                thumb = excluded.thumb,
                url = excluded.url,
                author = excluded.author,
                fetched_at = excluded.fetched_at
            """,
            p,
        )
        conn.commit()
    finally:
        conn.close()


def query_comment_pics(limit: int = 100) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM comment_pics ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_meme(m: dict) -> None:
    """写入或更新一个梗条目。列表字段在调用方序列化为 JSON 字符串。"""
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO memes (
                slug, name, aliases_json, origin, explanation,
                behavior_tags_json, exclude_json, search_queries_json, boundary, representative_url,
                representative_post_id, sort_order, updated_at
            ) VALUES (
                :slug, :name, :aliases_json, :origin, :explanation,
                :behavior_tags_json, :exclude_json, :search_queries_json, :boundary, :representative_url,
                :representative_post_id, :sort_order, :updated_at
            )
            ON CONFLICT(slug) DO UPDATE SET
                name = excluded.name,
                aliases_json = excluded.aliases_json,
                origin = excluded.origin,
                explanation = excluded.explanation,
                behavior_tags_json = excluded.behavior_tags_json,
                exclude_json = excluded.exclude_json,
                search_queries_json = excluded.search_queries_json,
                boundary = excluded.boundary,
                representative_url = excluded.representative_url,
                representative_post_id = excluded.representative_post_id,
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            m,
        )
        conn.commit()
    finally:
        conn.close()


def list_memes() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT m.*, COUNT(pm.post_id) AS post_count
            FROM memes m
            LEFT JOIN post_memes pm ON pm.meme_slug = m.slug
            GROUP BY m.slug
            ORDER BY m.sort_order, m.name
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_meme(slug: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM memes WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def clear_generated_links(slug: str) -> None:
    """重建前清除由同步流程生成的关键词与人工审定关联。"""
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM post_memes WHERE meme_slug = ? "
            "AND matched_by IN ('rule', 'curated')",
            (slug,),
        )
        conn.commit()
    finally:
        conn.close()


def link_post_meme(
    post_id: str,
    meme_slug: str,
    matched_term: str,
    matched_by: str = "rule",
    confidence: float = 1.0,
) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO post_memes
                (post_id, meme_slug, matched_by, matched_term, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (post_id, meme_slug, matched_by, matched_term, confidence),
        )
        conn.commit()
    finally:
        conn.close()


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_posts_by_text(term: str, excludes: tuple[str, ...] = ()) -> list[dict]:
    clauses = ["text_plain LIKE ? ESCAPE '\\'"]
    params: list[str] = [f"%{_like_escape(term)}%"]
    for value in excludes:
        clauses.append("text_plain NOT LIKE ? ESCAPE '\\'")
        params.append(f"%{_like_escape(value)}%")
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id FROM posts WHERE " + " AND ".join(clauses), tuple(params)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def find_post_by_mid(mid: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM posts WHERE mid = ?", (mid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def query_meme_posts(
    slug: str, limit: int = 200, order: str = "earliest", offset: int = 0
) -> list[dict]:
    orders = {
        "earliest": "p.ts ASC, p.id ASC",
        "new": "p.ts ASC, p.id ASC",
        "latest": "p.ts DESC, p.id DESC",
        "hot": "(p.attitudes_count + 5 * p.reposts_count + 3 * p.comments_count) DESC, p.ts DESC, p.id DESC",
        "likes": "p.attitudes_count DESC, p.ts DESC, p.id DESC",
    }
    clause = orders.get(order, orders["earliest"])
    conn = connect()
    try:
        rows = conn.execute(
            f"""
            SELECT p.*, pm.matched_term, pm.matched_by
            FROM post_memes pm
            JOIN posts p ON p.id = pm.post_id
            WHERE pm.meme_slug = ?
            ORDER BY {clause}
            LIMIT ? OFFSET ?
            """,
            (slug, limit, max(0, offset)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_meme_posts(slug: str) -> int:
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM post_memes WHERE meme_slug = ?", (slug,)
        ).fetchone()["c"]
    finally:
        conn.close()


def get_post_meme_links(post_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT meme_slug, matched_term FROM post_memes WHERE post_id = ?",
            (post_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_rule_categories(post_id: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM post_categories WHERE post_id = ? AND matched_by = 'rule'",
            (post_id,),
        )
        conn.commit()
    finally:
        conn.close()


def add_post_category(
    post_id: str,
    category: str,
    matched_by: str = "rule",
    matched_term: str = "",
) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO post_categories
                (post_id, category, matched_by, matched_term, classified_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(post_id, category) DO UPDATE SET
                matched_by = excluded.matched_by,
                matched_term = excluded.matched_term,
                classified_at = excluded.classified_at
            """,
            (post_id, category, matched_by, matched_term),
        )
        conn.commit()
    finally:
        conn.close()


def get_video_cache(key: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM video_cache WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_video_cache(key: str, url: str, poster: str, expires_at: int) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO video_cache (key, url, poster, expires_at, fetched_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                url = excluded.url,
                poster = excluded.poster,
                expires_at = excluded.expires_at,
                fetched_at = excluded.fetched_at
            """,
            (key, url, poster or "", int(expires_at or 0)),
        )
        conn.commit()
    finally:
        conn.close()


def invalidate_video_cache(key: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE video_cache SET expires_at = 0 WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def reserve_weibo_request(
    request_day: str,
    category: str,
    endpoint: str,
    daily_limit: int,
) -> int | None:
    """原子预占一条当日请求额度，成功时返回预占后的累计请求数。"""
    conn = connect_ledger()
    try:
        conn.execute("BEGIN IMMEDIATE")
        used = conn.execute(
            "SELECT COUNT(*) FROM weibo_request_ledger WHERE request_day = ?",
            (request_day,),
        ).fetchone()[0]
        if used >= daily_limit:
            conn.rollback()
            return None
        conn.execute(
            """
            INSERT INTO weibo_request_ledger
                (request_day, category, endpoint, requested_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (request_day, category, endpoint),
        )
        conn.commit()
        return used + 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def weibo_request_stats(request_day: str) -> dict:
    conn = connect_ledger()
    try:
        rows = conn.execute(
            """
            SELECT category, COUNT(*) AS request_count
            FROM weibo_request_ledger
            WHERE request_day = ?
            GROUP BY category
            ORDER BY category
            """,
            (request_day,),
        ).fetchall()
        categories = {
            row["category"]: row["request_count"] for row in rows
        }
        return {
            "day": request_day,
            "total": sum(categories.values()),
            "categories": categories,
        }
    finally:
        conn.close()


def clean_empty_pics() -> dict:
    """清理 pics_json 中 thumb/url 都为空的图片项，并修正 has_pics。"""
    conn = connect()
    changed = 0
    emptied = 0
    try:
        rows = conn.execute("SELECT id, pics_json, has_pics FROM posts").fetchall()
        for row in rows:
            try:
                pics = json.loads(row["pics_json"] or "[]")
            except ValueError:
                pics = []
            good = [p for p in pics if p.get("thumb") or p.get("url")]
            new_json = json.dumps(good, ensure_ascii=False)
            new_flag = 1 if good else 0
            if new_json != (row["pics_json"] or "[]") or new_flag != int(row["has_pics"] or 0):
                conn.execute(
                    "UPDATE posts SET pics_json = ?, has_pics = ? WHERE id = ?",
                    (new_json, new_flag, row["id"]),
                )
                changed += 1
                if not good and int(row["has_pics"] or 0):
                    emptied += 1
        conn.commit()
        return {"changed": changed, "emptied": emptied}
    finally:
        conn.close()
