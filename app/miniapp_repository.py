"""小程序只读接口的数据访问层。

本地默认读取 SQLite；微信云托管通过环境变量切换到 MySQL。查询只允许使用
固定 feed、排序和搜索条件，不向数据库暴露原始 SQL。
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

import store
from filters import like_pattern

FEED_LABELS = {
    "home": "全部微博",
    "memes": "全部罪证",
    "fan-memes": "全部二创",
    "interactions": "互动",
    "mengqian-cp": "萌钱CP",
    "photos": "高清图",
    "entity-aliases": "实体别名",
}

FEED_CATEGORIES = {
    "memes": "memes",
    "fan-memes": "fan_memes",
    "interactions": "interactions",
    "mengqian-cp": "interaction_mengqian_cp",
    "photos": "photos",
    "entity-aliases": "entity_aliases",
}

POST_SORTS = {"hot", "latest", "earliest"}
SEARCH_SORTS = {"relevance", "latest", "hot"}
MINIAPP_SORTS = {
    "hot": "热门",
    "latest": "最新",
    "earliest": "最早",
}
SEARCH_SORT_LABELS = {
    "relevance": "相关度",
    "latest": "最新",
    "hot": "热门",
}

POST_COLUMNS = """
    id, mid, uid, screen_name, created_at, ts, text_plain,
    reposts_count, comments_count, attitudes_count, source,
    is_longtext, is_video, is_live, has_pics, pics_json, retweeted_user
"""


def _terms(value: str) -> list[str]:
    return [item for item in re.split(r"\s+", (value or "").strip()) if item]


class Repository:
    backend_name: str = "sqlite"
    placeholder: str = "?"
    search_like_sql: str = "text_plain LIKE ? ESCAPE '\\'"

    def _query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
        raise NotImplementedError

    def _query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict | None:
        rows = self._query_all(sql, params)
        return rows[0] if rows else None

    def _order_sql(self, sort: str, *, alias: str = "") -> str:
        p = f"{alias}." if alias else ""
        engagement = (
            f"({p}attitudes_count + 5 * {p}reposts_count + "
            f"3 * {p}comments_count)"
        )
        if sort == "latest":
            return f"{p}ts DESC, {p}id DESC"
        if sort == "earliest":
            return f"{p}ts ASC, {p}id ASC"
        return f"{engagement} DESC, {p}ts DESC, {p}id DESC"

    def _greatest(self, minimum: int, expression: str) -> str:
        return f"MAX({minimum}, {expression})"

    def _relevance_order(
        self, terms: list[str], *, alias: str = ""
    ) -> tuple[str, tuple[Any, ...]]:
        p = f"{alias}." if alias else ""
        if not terms:
            return self._order_sql("hot", alias=alias), ()
        parts: list[str] = []
        params: list[Any] = []
        phrase = " ".join(terms)
        if phrase:
            phrase_score = (
                f"500 - INSTR(LOWER({p}text_plain), "
                f"LOWER({self.placeholder}))"
            )
            parts.append(
                f"(CASE WHEN INSTR(LOWER({p}text_plain), LOWER({self.placeholder})) > 0 "
                f"THEN 2000 + {self._greatest(0, phrase_score)} ELSE 0 END)"
            )
            params.extend([phrase, phrase])
        for term in terms:
            term_score = (
                f"400 - INSTR(LOWER({p}text_plain), "
                f"LOWER({self.placeholder}))"
            )
            parts.append(
                f"(CASE WHEN INSTR(LOWER({p}text_plain), LOWER({self.placeholder})) > 0 "
                f"THEN {self._greatest(0, term_score)} ELSE 0 END)"
            )
            params.extend([term, term])
            parts.append(
                f"(((LENGTH(LOWER({p}text_plain)) - "
                f"LENGTH(REPLACE(LOWER({p}text_plain), "
                f"LOWER({self.placeholder}), ''))) / "
                f"{self._greatest(1, f'LENGTH({self.placeholder})')}) * 20)"
            )
            params.extend([term, term])
        score = " + ".join(parts)
        return (
            f"({score}) DESC, {self._order_sql('hot', alias=alias)}",
            tuple(params),
        )

    def _list_where(
        self, feed: str, meme: str = ""
    ) -> tuple[str, tuple[Any, ...]]:
        if meme:
            return (
                "EXISTS (SELECT 1 FROM post_memes pm "
                f"WHERE pm.post_id = posts.id AND pm.meme_slug = {self.placeholder})",
                (meme,),
            )
        category = FEED_CATEGORIES.get(feed)
        if not category:
            return "1=1", ()
        return (
            "EXISTS (SELECT 1 FROM post_categories pc "
            f"WHERE pc.post_id = posts.id AND pc.category = {self.placeholder})",
            (category,),
        )

    def list_posts(
        self,
        *,
        feed: str,
        meme: str,
        page: int,
        page_size: int,
        sort: str,
    ) -> dict:
        where, params = self._list_where(feed, meme)
        total_row = self._query_one(
            f"SELECT COUNT(*) AS total FROM posts WHERE {where}", params
        )
        total = int((total_row or {}).get("total") or 0)
        offset = (page - 1) * page_size
        rows = self._query_all(
            f"SELECT {POST_COLUMNS} FROM posts WHERE {where} "
            f"ORDER BY {self._order_sql(sort)} LIMIT {self.placeholder} OFFSET {self.placeholder}",
            (*params, page_size, offset),
        )
        return {
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "has_more": offset + len(rows) < total,
        }

    def search_posts(
        self,
        *,
        query: str,
        page: int,
        page_size: int,
        sort: str,
    ) -> dict:
        terms = _terms(query)
        clauses: list[str] = []
        params: list[Any] = []
        for term in terms:
            clauses.append(self.search_like_sql)
            params.append(like_pattern(term))
        where = " AND ".join(clauses) if clauses else "1=0"
        total_row = self._query_one(
            f"SELECT COUNT(*) AS total FROM posts WHERE {where}", tuple(params)
        )
        total = int((total_row or {}).get("total") or 0)
        order_sql, order_params = (
            self._relevance_order(terms)
            if sort == "relevance"
            else (self._order_sql(sort), ())
        )
        offset = (page - 1) * page_size
        rows = self._query_all(
            f"SELECT {POST_COLUMNS} FROM posts WHERE {where} "
            f"ORDER BY {order_sql} LIMIT {self.placeholder} OFFSET {self.placeholder}",
            (*params, *order_params, page_size, offset),
        )
        return {
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "has_more": offset + len(rows) < total,
        }

    def get_post(self, post_id: str) -> dict | None:
        return self._query_one(
            "SELECT * FROM posts WHERE id = ?", (post_id,)
        )

    def post_labels(self, post_id: str) -> dict:
        categories = self._query_all(
            "SELECT category FROM post_categories WHERE post_id = ? "
            "ORDER BY category",
            (post_id,),
        )
        memes = self._query_all(
            "SELECT m.slug, m.name, m.kind FROM post_memes pm "
            "JOIN memes m ON m.slug = pm.meme_slug "
            "WHERE pm.post_id = ? ORDER BY m.sort_order, m.name",
            (post_id,),
        )
        return {
            "categories": [row["category"] for row in categories],
            "memes": memes,
        }

    def list_memes(self, kind: str) -> list[dict]:
        if kind not in {"behavior", "fan_fiction"}:
            return []
        return self._query_all(
            """
            SELECT m.slug, m.name, m.kind, m.origin, m.explanation, m.boundary,
                   COUNT(pm.post_id) AS post_count
            FROM memes m
            LEFT JOIN post_memes pm ON pm.meme_slug = m.slug
            WHERE m.kind = ? AND m.status = 'active'
            GROUP BY m.slug, m.name, m.kind, m.origin, m.explanation, m.boundary,
                     m.sort_order
            ORDER BY m.sort_order, m.name
            """,
            (kind,),
        )

    def get_meme(self, slug: str) -> dict | None:
        return self._query_one(
            """
            SELECT slug, name, kind, origin, explanation, boundary,
                   representative_url, representative_post_id
            FROM memes
            WHERE slug = ? AND status = 'active'
            """,
            (slug,),
        )

    def category_counts(self) -> dict[str, int]:
        rows = self._query_all(
            "SELECT category, COUNT(*) AS count FROM post_categories GROUP BY category"
        )
        return {str(row["category"]): int(row["count"]) for row in rows}

    def time_range(self) -> dict:
        row = self._query_one(
            "SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM posts WHERE ts > 0"
        ) or {}
        return {
            "min_ts": int(row["min_ts"]) if row.get("min_ts") is not None else None,
            "max_ts": int(row["max_ts"]) if row.get("max_ts") is not None else None,
        }

    def count_posts(self) -> int:
        row = self._query_one("SELECT COUNT(*) AS count FROM posts") or {}
        return int(row.get("count") or 0)


class SQLiteRepository(Repository):
    def _query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
        conn = store.connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


class MySQLRepository(Repository):
    backend_name: str = "mysql"
    placeholder = "%s"
    search_like_sql = "text_plain LIKE %s"

    def _greatest(self, minimum: int, expression: str) -> str:
        return f"GREATEST({minimum}, {expression})"

    def _mysql_sql(self, sql: str) -> str:
        return sql.replace("?", "%s")

    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError("MySQL 模式需要安装 PyMySQL") from exc
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
            connect_timeout=int(os.environ.get("MINIAPP_DB_CONNECT_TIMEOUT", "8")),
            read_timeout=int(os.environ.get("MINIAPP_DB_READ_TIMEOUT", "10")),
            write_timeout=int(os.environ.get("MINIAPP_DB_WRITE_TIMEOUT", "10")),
        )

    def _query_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(self._mysql_sql(sql), params)
                return list(cursor.fetchall())
        finally:
            conn.close()


def get_repository() -> Repository:
    if os.environ.get("MINIAPP_DB_BACKEND", "sqlite").strip().lower() == "mysql":
        return MySQLRepository()
    return SQLiteRepository()
