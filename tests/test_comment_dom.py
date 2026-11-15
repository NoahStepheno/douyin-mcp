"""DOM-level tests: real captured panel fixture + collector state branches."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.browser.comment_panel import (
    open_comment_panel,
    read_comment_items,
)
from douyin_creator_mcp.browser.comments import (
    COMMENT_PARSER_VERSION,
    parse_total_comment_count,
    STATE_COMMENTS_DISABLED,
    STATE_EMPTY,
    STATE_RISK_CONTROL,
    STATE_UNKNOWN,
    STATE_VIDEO_NOT_PUBLIC,
    collect_comments,
)

from .fixtures_real_panel import (
    EMPTY_PANEL_HTML,
    MISMATCH_PANEL_HTML,
    REAL_PANEL_HTML,
    SEMANTIC_PANEL_HTML,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
COLLECTED_AT = datetime(2026, 11, 15, 12, 0, 0, tzinfo=SHANGHAI)
VIDEO_ID = "7707861402739952923"
EXPECTED_NICKNAMES = [
    "用户A",
    "用户B",
    "用户A",
    "用户C",
    "用户D",
    "用户F",
    "用户B",
]


def _render(page: Any, html: str) -> None:
    page.set_content(html, wait_until="domcontentloaded")


def _collect(page: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("settle_ms", 10)
    kwargs.setdefault("panel_timeout_ms", 50)
    return collect_comments(
        page, video_id=VIDEO_ID, collected_at=COLLECTED_AT, **kwargs
    )


@pytest.mark.parametrize(
    ("variant", "html"),
    [("data_e2e_hook", REAL_PANEL_HTML), ("semantic_only", SEMANTIC_PANEL_HTML)],
)
def test_real_fixture_hits_v2_anchors(
    dom_page: Any, variant: str, html: str
) -> None:
    """Anti-self-证明: the captured fixture must be read by the production path."""

    _render(dom_page, html)
    observed = read_comment_items(dom_page)

    assert observed["reply_anchor_count"] == 7, variant
    assert observed["panel_found"] is True, variant
    assert len(observed["items"]) == 7, variant
    assert [item["nickname"] for item in observed["items"]] == EXPECTED_NICKNAMES
    assert [item["content"] for item in observed["items"]] == [
        "原文呢，求发出来",
        "在评论区",
        "这不就是评论区",
        "老弟你带我一个",
        "总结视频中观点",
        "...",
        "Dan Koe 原文 https://example.invalid/removed",
    ]
    assert [item["time_text"] for item in observed["items"]][:3] == [
        "1小时前·云南",
        "53分钟前·云南",
        "52分钟前·云南",
    ]
    assert sum(1 for item in observed["items"] if item["is_author_reply"]) == 2
    # Real header only renders "全部评论", no numeric total.
    assert parse_total_comment_count(observed["header_text"]) is None


def test_normal_panel_collects_comments(dom_page: Any) -> None:
    _render(dom_page, REAL_PANEL_HTML)
    result = _collect(dom_page)

    assert result["state"] == "comments"
    assert result["parser_version"] == COMMENT_PARSER_VERSION == "comment-panel-v2"
    assert result["total_comment_count"] is None
    assert result["exhausted"] is True
    assert [item["author_nickname"] for item in result["items"]] == EXPECTED_NICKNAMES
    pinned, author_reply = result["items"][0], result["items"][1]
    assert pinned["is_pinned"] is False
    assert author_reply["is_author_reply"] is True
    assert pinned["like_count"] == 0
    assert pinned["reply_count"] is None
    assert pinned["posted_at_precision"] == "approx"
    assert pinned["posted_at"] == "2026-11-15T11:00:00+08:00"


def test_pinned_comment_keeps_dom_order(dom_page: Any) -> None:
    _render(dom_page, REAL_PANEL_HTML)
    result = _collect(dom_page)
    assert [item["author_nickname"] for item in result["items"]] == EXPECTED_NICKNAMES
    assert all(item["is_pinned"] is False for item in result["items"])


def test_empty_state_is_a_positive_signal(dom_page: Any) -> None:
    _render(dom_page, EMPTY_PANEL_HTML)
    result = _collect(dom_page)

    assert result["state"] == STATE_EMPTY
    assert result["items"] == []
    assert result["total_comment_count"] == 0


def test_unrecognized_structure_is_not_empty(dom_page: Any) -> None:
    _render(dom_page, MISMATCH_PANEL_HTML)
    result = _collect(dom_page)

    assert result["state"] == STATE_UNKNOWN
    assert result["reason"] == "panel_not_opened"
    assert result["items"] == []


def test_limit_truncation_marks_not_exhausted(dom_page: Any) -> None:
    _render(dom_page, REAL_PANEL_HTML)
    result = _collect(dom_page, limit=3)

    assert result["raw_item_count"] == 3
    assert result["seen_item_count"] == 7
    assert result["exhausted"] is False


def test_panel_already_open_without_clickable_button(dom_page: Any) -> None:
    """Real detail pages render the comment column without any click needed."""

    _render(dom_page, REAL_PANEL_HTML)
    opened = open_comment_panel(dom_page, settle_ms=10, timeout_ms=50)

    assert opened["opened"] is True
    assert opened["strategy"] == "already_open"
    assert opened["attempts"] == []

    result = _collect(dom_page)
    assert result["opened_by"] == "already_open"
    assert len(result["items"]) == 7


class FakePanelPage:
    """Minimal page double keyed by the script being evaluated."""

    def __init__(
        self,
        *,
        signals: dict[str, Any] | None = None,
        items: dict[str, Any] | None = None,
    ) -> None:
        self.signals = signals or {}
        self.items = items or {}
        self.evaluate_calls = 0
        self.scroll_calls = 0
        self.waits = 0

    def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluate_calls += 1
        if "scrollTop" in script:
            self.scroll_calls += 1
            return "panel"
        if "reply_button_seen" in script and "risk_control_seen" in script:
            return dict(self.signals)
        if "reply_anchor_count" in script:
            return dict(self.items)
        return {}

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits += 1


def _stub_opener(result: dict[str, Any]) -> Any:
    def opener(page: Any, **kwargs: Any) -> dict[str, Any]:
        return result

    return opener


def test_risk_control_stops_without_retry_or_scroll() -> None:
    page = FakePanelPage(signals={"risk_control_seen": True, "login_wall": False})
    result = collect_comments(
        page,
        video_id=VIDEO_ID,
        collected_at=COLLECTED_AT,
        panel_opener=_stub_opener({"opened": False}),
    )

    assert result["state"] == STATE_RISK_CONTROL
    assert page.evaluate_calls == 1
    assert page.scroll_calls == 0
    assert page.waits == 0


def test_panel_not_opened_reason() -> None:
    page = FakePanelPage(
        signals={},
        items={"reply_anchor_count": 0, "panel_found": False, "empty_seen": False},
    )
    result = collect_comments(
        page,
        video_id=VIDEO_ID,
        collected_at=COLLECTED_AT,
        panel_opener=_stub_opener(
            {"opened": False, "strategy": None, "signals": {}, "risk_control_seen": False}
        ),
    )

    assert result["state"] == STATE_UNKNOWN
    assert result["reason"] == "panel_not_opened"


def test_panel_opened_without_items_reason() -> None:
    page = FakePanelPage(
        signals={},
        items={"reply_anchor_count": 0, "panel_found": True, "empty_seen": False},
    )
    result = collect_comments(
        page,
        video_id=VIDEO_ID,
        collected_at=COLLECTED_AT,
        panel_opener=_stub_opener(
            {"opened": True, "strategy": "count_button", "signals": {}, "risk_control_seen": False}
        ),
    )

    assert result["reason"] == "panel_opened_but_no_items"


def test_panel_opened_but_containers_not_matched_reason() -> None:
    page = FakePanelPage(
        signals={},
        items={"reply_anchor_count": 0, "panel_found": False, "empty_seen": False},
    )
    result = collect_comments(
        page,
        video_id=VIDEO_ID,
        collected_at=COLLECTED_AT,
        panel_opener=_stub_opener(
            {"opened": True, "strategy": "aria_label", "signals": {}, "risk_control_seen": False}
        ),
    )

    assert result["reason"] == "selectors_not_matched"


def test_comments_disabled_state_is_distinct() -> None:
    page = FakePanelPage(signals={"comments_disabled_seen": True})
    result = collect_comments(
        page,
        video_id=VIDEO_ID,
        collected_at=COLLECTED_AT,
        panel_opener=_stub_opener({"opened": False}),
    )

    assert result["state"] == STATE_COMMENTS_DISABLED
    assert result["items"] == []


def test_real_fixture_end_to_end_insights(dom_page: Any, settings: Any) -> None:
    from douyin_creator_mcp.services.comments import CommentsService

    from .fixtures import FakeExecutor

    _render(dom_page, REAL_PANEL_HTML)
    observation = _collect(dom_page)
    service = CommentsService(
        settings,
        db=None,
        browser_executor=FakeExecutor({VIDEO_ID: observation}),
        now=lambda: COLLECTED_AT,
    )

    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "completed"
    assert result["total"] == 7
    assert result["total_comment_count"] is None
    assert result["sampled"] is None
    assert result["completeness"] == "unknown"
    assert result["insights"]["excluded_author_replies"] == 2
    assert result["insights"]["category_details"]["求链接"]["count"] >= 1
    assert "原文" in result["insights"]["category_details"]["求链接"]["matched_terms"]
    for comment in result["comments"]:
        assert comment["posted_at"] is not None
        assert comment["posted_at_precision"] == "approx"


def test_video_not_public_stops_without_opening_panel() -> None:
    page = FakePanelPage(signals={"video_not_public_seen": True})
    result = collect_comments(
        page,
        video_id=VIDEO_ID,
        collected_at=COLLECTED_AT,
        panel_opener=_stub_opener({"opened": False}),
    )

    assert result["state"] == STATE_VIDEO_NOT_PUBLIC
    assert result["reason"] == "video_not_public"
    assert page.evaluate_calls == 1
    assert page.scroll_calls == 0
