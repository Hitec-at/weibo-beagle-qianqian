"""微博原文链接的共享生成规则。"""
from __future__ import annotations


def original_url(post: dict | None) -> str:
    """从微博记录生成可直接打开的原文链接。"""
    if not post:
        return ""
    uid = str(post.get("uid") or "").strip()
    mid = str(post.get("mid") or "").strip()
    if uid and mid:
        return f"https://weibo.com/{uid}/{mid}"
    post_id = str(post.get("id") or post.get("post_id") or "").strip()
    if post_id:
        return f"https://m.weibo.cn/detail/{post_id}"
    return ""


def markdown_link(post_id: str, url: str, label: str = "微博原文") -> str:
    """生成保留微博 id 的可点击 Markdown 链接。"""
    post_id = str(post_id or "").strip()
    if url:
        return f"[{label}]({url})（`{post_id}`）" if post_id else f"[{label}]({url})"
    return f"`{post_id}`" if post_id else "微博 id 缺失"
