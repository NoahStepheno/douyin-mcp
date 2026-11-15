"""De-identified HTML fixtures and observation builders for comment tests."""

from __future__ import annotations

from typing import Any

# All service-level fixtures below are synthetic; DOM fixtures live in
# ``tests/fixtures_real_panel.py`` and come from a real captured panel.

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
    panel_found: bool = True,
    reason: str | None = None,
    final_url: str = "",
    read_count: int = 1,
) -> dict[str, Any]:
    materialized = list(items or [])
    return {
        "state": state,
        "reason": reason,
        "panel_found": panel_found,
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
        "final_url": final_url,
        "parser_version": "comment-panel-v2",
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
