"""DOM-level tests for the visible comment panel reader (no network)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.browser.comments import (
    STATE_EMPTY,
    STATE_RISK_CONTROL,
    STATE_UNKNOWN,
    collect_comments,
    parse_comment_time,
)

from .fixtures import (
    COMMENT_PANEL_HTML,
    EMPTY_COMMENT_PANEL_HTML,
    MISMATCH_HTML,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
COLLECTED_AT = datetime(2026, 11, 15, 12, 0, 0, tzinfo=SHANGHAI)
VIDEO_ID = "7000000000000000001"


@pytest.mark.parametrize(
    ("text", "expected_iso", "expected_precision"),
    [
        ("3小时前", "2026-11-15T09:00:00+08:00", "approx"),
        ("刚刚", "2026-11-15T12:00:00+08:00", "approx"),
        ("昨天 12:30", "2026-11-14T12:30:00+08:00", "approx"),
        ("2026-11-14 08:00", "2026-11-14T08:00:00+08:00", "exact"),
        ("2026-11-14", "2026-11-14T00:00:00+08:00", "approx"),
        ("不是时间", None, "unknown"),
        ("", None, "unknown"),
    ],
)
def test_parse_comment_time(
    text: str, expected_iso: str | None, expected_precision: str
) -> None:
    iso, precision = parse_comment_time(text, COLLECTED_AT)
    assert precision == expected_precision
    assert iso == expected_iso


def _render(page: Any, html: str) -> None:
    page.set_content(html, wait_until="domcontentloaded")


def test_normal_panel_collects_comments(dom_page: Any) -> None:
    _render(dom_page, COMMENT_PANEL_HTML)
    result = collect_comments(
        dom_page, video_id=VIDEO_ID, collected_at=COLLECTED_AT
    )

    assert result["state"] == "comments"
    assert result["total_comment_count"] == 12
    assert result["exhausted"] is True
    assert result["parser_version"] == "comment-panel-v1"
    assert [item["comment_id"] for item in result["items"]] == [
        "9000000000000000001",
        "9000000000000000002",
        "9000000000000000003",
    ]

    pinned, link_asker, negative = result["items"]
    assert pinned["is_pinned"] is True
    assert pinned["is_author_reply"] is True
    assert pinned["like_count"] == 12000
    assert pinned["reply_count"] == 2
    assert pinned["posted_at_precision"] == "approx"
    assert link_asker["content"] == "求个链接，在哪买？"
    assert link_asker["author_nickname"] == "测试昵称乙"
    assert link_asker["posted_at"] == "2026-11-14T12:30:00+08:00"
    assert negative["posted_at_precision"] == "exact"


def test_pinned_comment_keeps_dom_order(dom_page: Any) -> None:
    _render(dom_page, COMMENT_PANEL_HTML)
    result = collect_comments(
        dom_page, video_id=VIDEO_ID, collected_at=COLLECTED_AT
    )
    assert result["items"][0]["is_pinned"] is True
    assert [item["is_pinned"] for item in result["items"]] == [True, False, False]


def test_empty_state_is_a_positive_signal(dom_page: Any) -> None:
    _render(dom_page, EMPTY_COMMENT_PANEL_HTML)
    result = collect_comments(
        dom_page, video_id=VIDEO_ID, collected_at=COLLECTED_AT
    )
    assert result["state"] == STATE_EMPTY
    assert result["items"] == []


def test_unrecognized_structure_is_not_empty(dom_page: Any) -> None:
    _render(dom_page, MISMATCH_HTML)
    result = collect_comments(
        dom_page, video_id=VIDEO_ID, collected_at=COLLECTED_AT
    )
    assert result["state"] == STATE_UNKNOWN
    assert result["container_found"] is False
    assert result["items"] == []


def test_limit_truncation_marks_not_exhausted(dom_page: Any) -> None:
    html = COMMENT_PANEL_HTML.replace(
        '<div class="comment-end">没有更多评论</div>', ""
    )
    _render(dom_page, html)
    result = collect_comments(
        dom_page, video_id=VIDEO_ID, limit=2, collected_at=COLLECTED_AT
    )
    assert result["raw_item_count"] == 2
    assert result["seen_item_count"] == 3
    assert result["exhausted"] is False


class FakePanelPage:
    """Minimal page double that counts DOM reads and scrolls."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.evaluate_calls = 0
        self.scroll_calls = 0
        self.waits = 0

    def evaluate(self, script: str) -> dict[str, Any]:
        self.evaluate_calls += 1
        if "scrollTop" in script:
            self.scroll_calls += 1
            return "panel"
        index = min(self.evaluate_calls - 1, len(self.responses) - 1)
        return self.responses[index]

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits += 1


def test_risk_control_stops_without_retry_or_scroll() -> None:
    page = FakePanelPage(
        [
            {
                "state": STATE_RISK_CONTROL,
                "container_found": False,
                "items": [],
                "total_comment_count": None,
                "has_more_hint": None,
            }
        ]
    )
    result = collect_comments(page, video_id=VIDEO_ID, collected_at=COLLECTED_AT)

    assert result["state"] == STATE_RISK_CONTROL
    assert page.evaluate_calls == 1
    assert page.scroll_calls == 0
    assert page.waits == 0
