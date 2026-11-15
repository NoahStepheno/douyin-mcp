"""De-identified HTML fixtures and observation builders for comment tests."""

from __future__ import annotations

from typing import Any

# All nicknames, ids and texts below are synthetic; no real account data.
COMMENT_PANEL_HTML = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title></head>
<body>
  <div class="video-detail"><h1>测试作品</h1></div>
  <div class="comment-panel">
    <div class="comment-title">共 12 条评论</div>
    <div class="comment-list" data-e2e="comment-list">
      <div class="comment-item" data-comment-id="9000000000000000001">
        <span class="pin-tag">置顶</span>
        <span class="user-name">测试昵称甲</span>
        <span class="author-tag">作者</span>
        <div class="comment-content">置顶说明：新来的朋友先看这条</div>
        <span class="comment-time">3小时前</span>
        <span class="like-count">1.2万</span>
        <span class="reply-count">2条回复</span>
      </div>
      <div class="comment-item" data-comment-id="9000000000000000002">
        <span class="user-name">测试昵称乙</span>
        <div class="comment-content">求个链接，在哪买？</div>
        <span class="comment-time">昨天 12:30</span>
        <span class="like-count">36</span>
      </div>
      <div class="comment-item" data-comment-id="9000000000000000003">
        <span class="user-name">测试昵称丙</span>
        <div class="comment-content">太难用了，听不懂</div>
        <span class="comment-time">2026-11-14 08:00</span>
        <span class="like-count">5</span>
      </div>
    </div>
    <div class="comment-end">没有更多评论</div>
  </div>
</body></html>
"""

EMPTY_COMMENT_PANEL_HTML = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title></head>
<body>
  <div class="video-detail"><h1>测试作品</h1></div>
  <div class="comment-panel">
    <div class="comment-title">共 0 条评论</div>
    <div class="comment-list" data-e2e="comment-list">
      <div class="comment-empty">暂无评论，快来抢首评</div>
    </div>
  </div>
</body></html>
"""

MISMATCH_HTML = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title></head>
<body>
  <div id="app"><h1>测试作品</h1><p>页面内容加载中</p></div>
</body></html>
"""


def make_comment(
    comment_id: str,
    content: str,
    *,
    nickname: str = "测试昵称",
    like_count: int | None = 0,
    reply_count: int | None = 0,
    posted_at: str | None = "2026-11-15T04:00:00+08:00",
    precision: str = "approx",
    is_pinned: bool = False,
    is_author_reply: bool = False,
) -> dict[str, Any]:
    return {
        "comment_id": comment_id,
        "content": content,
        "author_nickname": nickname,
        "like_count": like_count,
        "reply_count": reply_count,
        "posted_at": posted_at,
        "posted_at_precision": precision,
        "is_pinned": is_pinned,
        "is_author_reply": is_author_reply,
    }


def make_observation(
    items: list[dict[str, Any]] | None = None,
    *,
    state: str = "comments",
    total_comment_count: int | None = None,
    exhausted: bool = False,
    raw_item_count: int | None = None,
    dropped_item_count: int = 0,
    container_found: bool = True,
    read_count: int = 1,
) -> dict[str, Any]:
    materialized = list(items or [])
    return {
        "state": state,
        "container_found": container_found,
        "items": materialized,
        "raw_item_count": (
            len(materialized) if raw_item_count is None else raw_item_count
        ),
        "seen_item_count": len(materialized),
        "dropped_item_count": dropped_item_count,
        "total_comment_count": total_comment_count,
        "exhausted": exhausted,
        "read_count": read_count,
        "parser_version": "comment-panel-v1",
    }


class FakeExecutor:
    """Return pre-built observations and record every command."""

    def __init__(self, observations: dict[str, dict[str, Any]]) -> None:
        self.observations = observations
        self.commands: list[Any] = []

    def execute(self, command: Any) -> dict[str, Any]:
        self.commands.append(command)
        observation = self.observations.get(command.video_id)
        if observation is None:
            raise AssertionError(f"unexpected video_id: {command.video_id}")
        return observation
