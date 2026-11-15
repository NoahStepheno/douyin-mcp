"""Pure parsing tests: real timestamps, panel totals, keyword rules v2."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.browser.comments import (
    is_not_public_url,
    parse_comment_time,
    parse_total_comment_count,
)
from douyin_creator_mcp.services.comment_insights import (
    COMMENT_KEYWORD_RULE_VERSION,
    DEFAULT_COMMENT_KEYWORDS,
    build_insights,
    load_keyword_rules,
    normalize_for_match,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
COLLECTED_AT = datetime(2026, 11, 15, 12, 0, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    ("text", "expected_iso", "expected_precision"),
    [
        # Real samples captured in run-1308, with region suffix.
        ("1小时前·云南", "2026-11-15T11:00:00+08:00", "approx"),
        ("53分钟前·云南", "2026-11-15T11:07:00+08:00", "approx"),
        ("15小时前·吉林", "2026-11-14T21:00:00+08:00", "approx"),
        ("1天前·河北", "2026-11-14T12:00:00+08:00", "approx"),
        ("1天前·浙江", "2026-11-14T12:00:00+08:00", "approx"),
        # Relative without suffix still approx.
        ("刚刚", "2026-11-15T12:00:00+08:00", "approx"),
        ("昨天 12:30", "2026-11-14T12:30:00+08:00", "approx"),
        ("前天", "2026-11-13T00:00:00+08:00", "approx"),
        # Year present -> exact, even date-only.
        ("2026-11-14", "2026-11-14T00:00:00+08:00", "exact"),
        ("2026年11月14日", "2026-11-14T00:00:00+08:00", "exact"),
        ("2026-11-14 08:00", "2026-11-14T08:00:00+08:00", "exact"),
        # Month-day only -> year inferred, must stay approx.
        ("11-14", "2026-11-14T00:00:00+08:00", "approx"),
        # Trailing noise from the real panel.
        ("1小时前·云南 作者赞过", "2026-11-15T11:00:00+08:00", "approx"),
        ("51分钟前·云南 置顶", "2026-11-15T11:09:00+08:00", "approx"),
        ("不是时间", None, "unknown"),
        ("", None, "unknown"),
    ],
)
def test_parse_comment_time_precision(
    text: str, expected_iso: str | None, expected_precision: str
) -> None:
    iso, precision = parse_comment_time(text, COLLECTED_AT)
    assert precision == expected_precision, text
    assert iso == expected_iso, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("全部评论 共 51 条评论", 51),
        ("共 51 条评论", 51),
        ("全部评论(51)", 51),
        ("评论（51）", 51),
        ("51条评论", 51),
        ("共 1.2万 条评论", 12_000),
        ("全部评论", None),
        ("暂时没有更多评论", None),
        ("", None),
    ],
)
def test_parse_total_comment_count(text: str, expected: int | None) -> None:
    assert parse_total_comment_count(text) == expected


def test_total_is_never_inferred_from_item_count() -> None:
    # No numeric total in the header: must stay null even when items exist.
    assert parse_total_comment_count("全部评论 用户A 用户B 回复 分享") is None


def test_keyword_rule_version_is_v2(settings) -> None:
    _, version, warnings = load_keyword_rules(settings.data_dir)
    assert version == "comment-keywords-v2"
    assert COMMENT_KEYWORD_RULE_VERSION == "comment-keywords-v2"
    assert warnings == []


def test_keyword_v2_matches_real_comment() -> None:
    rules, version, _ = load_keyword_rules(None)
    insights = build_insights(
        [
            {
                "comment_id": "1",
                "content": "原文呢，求发出来",
                "is_author_reply": False,
            }
        ],
        rules,
        version,
    )
    assert insights["categories"]["求链接"] == 1
    assert "原文" in insights["category_details"]["求链接"]["matched_terms"]
    assert insights["multi_label"] is True


def test_keyword_normalization_matches_spaced_terms() -> None:
    rules = {"求链接": ("求发", "原文")}
    insights = build_insights(
        [{"comment_id": "1", "content": "求 发 一下", "is_author_reply": False}],
        rules,
        "comment-keywords-v2",
    )
    assert insights["categories"]["求链接"] == 1
    assert insights["category_details"]["求链接"]["matched_terms"] == ["求发"]


def test_matched_terms_are_consistent_with_counts() -> None:
    rules, version, _ = load_keyword_rules(None)
    insights = build_insights(
        [
            {"comment_id": "1", "content": "在哪买？多少钱", "is_author_reply": False},
            {"comment_id": "2", "content": "如何操作", "is_author_reply": False},
            {"comment_id": "3", "content": "链接发我", "is_author_reply": False},
        ],
        rules,
        version,
    )
    for category, count in insights["categories"].items():
        detail = insights["category_details"][category]
        assert detail["count"] == count
        assert detail["matched_terms"] == sorted(detail["matched_terms"])
    assert insights["category_details"]["求链接"]["count"] == 2
    assert set(insights["category_details"]["求链接"]["matched_terms"]) <= set(
        DEFAULT_COMMENT_KEYWORDS["求链接"]
    )
    assert normalize_for_match("求 发 一下！") == "求发一下"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.douyin.com/jingxuan?previous_page=web_video_404_link", True),
        ("https://www.douyin.com/video/404", True),
        ("https://www.douyin.com/jingxuan?modal_id=7707861402739952923", False),
        ("https://www.douyin.com/video/7707861402739952923", False),
        ("", False),
    ],
)
def test_is_not_public_url(url: str, expected: bool) -> None:
    assert is_not_public_url(url) is expected
