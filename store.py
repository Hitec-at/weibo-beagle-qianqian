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
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    root_id TEXT NOT NULL,
    parent_id TEXT NOT NULL DEFAULT '',
    user_name TEXT NOT NULL DEFAULT '',
    text_plain TEXT NOT NULL DEFAULT '',
    like_count INTEGER NOT NULL DEFAULT 0,
    author_liked INTEGER NOT NULL DEFAULT 0,
    author_liked_known INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    is_reply INTEGER NOT NULL DEFAULT 0,
    reply_total INTEGER NOT NULL DEFAULT 0,
    reply_saved INTEGER NOT NULL DEFAULT 0,
    reply_complete INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_root ON comments(root_id);
CREATE TABLE IF NOT EXISTS comment_root_selections (
    post_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    page_rank INTEGER NOT NULL,
    like_rank INTEGER NOT NULL,
    selected_by_default INTEGER NOT NULL DEFAULT 0,
    selected_by_likes INTEGER NOT NULL DEFAULT 0,
    selection_order INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (post_id, comment_id)
);
CREATE INDEX IF NOT EXISTS idx_comment_root_selections_post
    ON comment_root_selections(post_id);
CREATE TABLE IF NOT EXISTS comment_threads (
    post_id TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL,
    roots_seen INTEGER NOT NULL DEFAULT 0,
    roots_selected INTEGER NOT NULL DEFAULT 0,
    replies_saved INTEGER NOT NULL DEFAULT 0,
    max_id TEXT NOT NULL DEFAULT '',
    complete INTEGER NOT NULL DEFAULT 0
);
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
CREATE TABLE IF NOT EXISTS baseline_runs (
    version TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    snapshot_cutoff TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS baseline_candidates (
    baseline_version TEXT NOT NULL,
    post_id TEXT NOT NULL,
    meme_slug TEXT NOT NULL,
    source TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    search_rank INTEGER,
    matched_term TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (
        baseline_version, post_id, meme_slug, source, query
    )
);
CREATE INDEX IF NOT EXISTS idx_baseline_candidates_meme
    ON baseline_candidates(baseline_version, meme_slug);
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
        comment_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(comments)")
        }
        if "author_liked_known" not in comment_columns:
            conn.execute(
                "ALTER TABLE comments ADD COLUMN author_liked_known "
                "INTEGER NOT NULL DEFAULT 0"
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


UPSERT_COMMENT_SQL = """
    INSERT INTO comments (
        id, post_id, root_id, parent_id, user_name, text_plain,
        like_count, author_liked, author_liked_known, created_at, is_reply,
        reply_total, reply_saved, reply_complete, fetched_at
    ) VALUES (
        :id, :post_id, :root_id, :parent_id, :user_name, :text_plain,
        :like_count, :author_liked, :author_liked_known, :created_at, :is_reply,
        :reply_total, :reply_saved, :reply_complete, :fetched_at
    )
    ON CONFLICT(id) DO UPDATE SET
        post_id = excluded.post_id,
        root_id = excluded.root_id,
        parent_id = excluded.parent_id,
        user_name = excluded.user_name,
        text_plain = excluded.text_plain,
        like_count = excluded.like_count,
        author_liked = excluded.author_liked,
        author_liked_known = excluded.author_liked_known,
        created_at = excluded.created_at,
        is_reply = excluded.is_reply,
        reply_total = excluded.reply_total,
        reply_saved = excluded.reply_saved,
        reply_complete = excluded.reply_complete,
        fetched_at = excluded.fetched_at
"""


UPSERT_COMMENT_THREAD_SQL = """
    INSERT INTO comment_threads (
        post_id, fetched_at, source, roots_seen, roots_selected,
        replies_saved, max_id, complete
    ) VALUES (
        :post_id, :fetched_at, :source, :roots_seen, :roots_selected,
        :replies_saved, :max_id, :complete
    )
    ON CONFLICT(post_id) DO UPDATE SET
        fetched_at = excluded.fetched_at,
        source = excluded.source,
        roots_seen = excluded.roots_seen,
        roots_selected = excluded.roots_selected,
        replies_saved = excluded.replies_saved,
        max_id = excluded.max_id,
        complete = excluded.complete
"""


def upsert_comment(comment: dict) -> None:
    conn = connect()
    try:
        conn.execute(UPSERT_COMMENT_SQL, comment)
        conn.commit()
    finally:
        conn.close()


def upsert_comment_thread(thread: dict) -> None:
    conn = connect()
    try:
        conn.execute(UPSERT_COMMENT_THREAD_SQL, thread)
        conn.commit()
    finally:
        conn.close()


def replace_comment_page(
    post_id: str,
    comments: list[dict],
    selections: list[dict],
    thread: dict,
) -> None:
    """原子替换一条微博的首轮评论包，避免重跑混入过期首页结果。"""
    conn = connect()
    try:
        conn.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        conn.execute(
            "DELETE FROM comment_root_selections WHERE post_id = ?", (post_id,)
        )
        for comment in comments:
            conn.execute(UPSERT_COMMENT_SQL, comment)
        for selection in selections:
            conn.execute(
                """
                INSERT INTO comment_root_selections (
                    post_id, comment_id, page_rank, like_rank,
                    selected_by_default, selected_by_likes,
                    selection_order, fetched_at
                ) VALUES (
                    :post_id, :comment_id, :page_rank, :like_rank,
                    :selected_by_default, :selected_by_likes,
                    :selection_order, :fetched_at
                )
                """,
                selection,
            )
        conn.execute(UPSERT_COMMENT_THREAD_SQL, thread)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def comments_for_post(post_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                c.*,
                s.page_rank,
                s.like_rank,
                s.selected_by_default,
                s.selected_by_likes,
                s.selection_order
            FROM comments c
            LEFT JOIN comment_root_selections s
                ON s.post_id = c.post_id AND s.comment_id = c.id
            WHERE c.post_id = ?
            ORDER BY
                c.is_reply,
                CASE WHEN c.is_reply = 0 THEN s.selection_order ELSE 0 END,
                c.like_count DESC,
                c.id
            """,
            (post_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def comment_thread(post_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM comment_threads WHERE post_id = ?",
            (post_id,),
        ).fetchone()
        return dict(row) if row else None
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


def rename_meme_slug(old_slug: str, new_slug: str) -> bool:
    """把旧 slug 的条目、微博关联和 baseline 证据迁移到新 slug。"""
    if not old_slug or not new_slug or old_slug == new_slug:
        return False

    conn = connect()
    try:
        conn.execute("BEGIN")
        old = conn.execute(
            "SELECT slug FROM memes WHERE slug = ?", (old_slug,)
        ).fetchone()
        if not old:
            conn.rollback()
            return False

        conn.execute(
            """
            INSERT OR IGNORE INTO post_memes (
                post_id, meme_slug, matched_by, matched_term, confidence, updated_at
            )
            SELECT post_id, ?, matched_by, matched_term, confidence, updated_at
            FROM post_memes
            WHERE meme_slug = ?
            """,
            (new_slug, old_slug),
        )
        conn.execute("DELETE FROM post_memes WHERE meme_slug = ?", (old_slug,))

        conn.execute(
            """
            INSERT OR IGNORE INTO baseline_candidates (
                baseline_version, post_id, meme_slug, source, query,
                search_rank, matched_term, status, observed_at
            )
            SELECT baseline_version, post_id, ?, source, query,
                   search_rank, matched_term, status, observed_at
            FROM baseline_candidates
            WHERE meme_slug = ?
            """,
            (new_slug, old_slug),
        )
        conn.execute("DELETE FROM baseline_candidates WHERE meme_slug = ?", (old_slug,))

        new = conn.execute(
            "SELECT slug FROM memes WHERE slug = ?", (new_slug,)
        ).fetchone()
        if new:
            conn.execute("DELETE FROM memes WHERE slug = ?", (old_slug,))
        else:
            conn.execute(
                "UPDATE memes SET slug = ? WHERE slug = ?", (new_slug, old_slug)
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
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


def create_baseline_run(version: str, snapshot_cutoff: str) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO baseline_runs
                (version, created_at, snapshot_cutoff, summary_json)
            VALUES (?, datetime('now'), ?, '{}')
            """,
            (version, snapshot_cutoff),
        )
        conn.commit()
    finally:
        conn.close()


def add_baseline_candidate(
    baseline_version: str,
    post_id: str,
    meme_slug: str,
    source: str,
    query: str,
    matched_term: str,
    status: str,
    search_rank: int | None = None,
) -> None:
    conn = connect()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO baseline_candidates
                (
                    baseline_version, post_id, meme_slug, source, query,
                    search_rank, matched_term, status, observed_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                baseline_version,
                post_id,
                meme_slug,
                source,
                query,
                search_rank,
                matched_term,
                status,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def finish_baseline_run(version: str, summary: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE baseline_runs SET summary_json = ? WHERE version = ?",
            (json.dumps(summary, ensure_ascii=False), version),
        )
        conn.commit()
    finally:
        conn.close()


def get_baseline_run(version: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM baseline_runs WHERE version = ?",
            (version,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["summary"] = json.loads(result.pop("summary_json") or "{}")
        return result
    finally:
        conn.close()


def baseline_candidate_stats(version: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                meme_slug,
                status,
                source,
                COUNT(DISTINCT post_id) AS post_count
            FROM baseline_candidates
            WHERE baseline_version = ?
            GROUP BY meme_slug, status, source
            ORDER BY meme_slug, status, source
            """,
            (version,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def baseline_query_stats(version: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                meme_slug,
                source,
                status,
                query,
                COUNT(DISTINCT post_id) AS post_count,
                MIN(search_rank) AS best_rank,
                MAX(search_rank) AS worst_rank,
                ROUND(AVG(search_rank), 1) AS average_rank
            FROM baseline_candidates
            WHERE baseline_version = ?
            GROUP BY meme_slug, source, status, query
            ORDER BY meme_slug, source, status, query
            """,
            (version,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def baseline_review_rows(version: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                c.meme_slug,
                c.post_id,
                c.source,
                c.query,
                c.search_rank,
                c.matched_term,
                c.status,
                c.observed_at,
                p.created_at,
                p.text_plain,
                p.reposts_count,
                p.comments_count,
                p.attitudes_count,
                p.retweeted_user
            FROM baseline_candidates c
            LEFT JOIN posts p ON p.id = c.post_id
            WHERE c.baseline_version = ?
            ORDER BY
                c.meme_slug,
                CASE c.status WHEN 'published' THEN 0 ELSE 1 END,
                c.source,
                CASE WHEN c.search_rank IS NULL THEN 1 ELSE 0 END,
                c.search_rank,
                c.post_id
            """,
            (version,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def clear_post_categories(post_id: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM post_categories WHERE post_id = ?", (post_id,))
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
