"""Service-level contract tests for douyin_browser_get_video_comments."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.services.comments import CommentsService

from .fixtures import FakeExecutor, make_comment, make_observation

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=SHANGHAI)
VIDEO_ID = "7000000000000000001"
OTHER_VIDEO_ID = "7000000000000000002"


def build_service(settings: Any, observations: dict[str, Any]) -> tuple[CommentsService, FakeExecutor]:
    executor = FakeExecutor(observations)
    service = CommentsService(
        settings,
        db=None,
        browser_executor=executor,
        now=lambda: NOW,
    )
    return service, executor


def test_normal_result_contract_and_insights(settings: Any) -> None:
    comments = [
        make_comment("1", "求个链接，在哪买？", nickname="测试昵称乙"),
        make_comment("2", "谢谢，学到了", nickname="测试昵称丁", precision="exact"),
        make_comment(
            "3",
            "作者回复：价格看主页",
            is_author_reply=True,
        ),
    ]
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments, total_comment_count=3, exhausted=True
            )
        },
    )

    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "completed"
    assert result["error_code"] is None
    assert result["video_id"] == VIDEO_ID
    assert result["fetched_at"] == "2026-11-15T12:00:00+08:00"
    assert result["total"] == 3
    assert result["total_comment_count"] == 3
    assert result["sampled"] is False
    assert result["completeness"] == "complete"
    assert result["rate_limited"] is False
    assert result["has_more"] is False
    assert result["next_cursor"] is None
    assert result["parser_version"] == "comment-panel-v2"

    insights = result["insights"]
    assert insights["multi_label"] is True
    assert insights["keyword_rule_version"] == "comment-keywords-v2"
    assert insights["excluded_author_replies"] == 1
    assert insights["total_comments"] == 2
    assert insights["categories"]["求链接"] == 1
    assert insights["categories"]["正反馈"] == 1
    assert {"word": "链接", "count": 1} in insights["top_words"]


def test_empty_comments_is_completed(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [], state="empty", total_comment_count=0, exhausted=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "completed"
    assert result["comments"] == []
    assert result["total"] == 0
    assert result["sampled"] is False
    assert result["completeness"] == "complete"


def test_dom_mismatch_is_parse_failed_not_completed(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [],
                state="unknown",
                container_found=False,
                exhausted=True,
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "parse_failed"
    assert result["error_code"] == "DOM_MISMATCH"
    assert result["comments"] == []
    assert result["sampled"] is None
    assert result["completeness"] == "unknown"
    assert "reason" in result


@pytest.mark.parametrize(
    "reason",
    ["panel_not_opened", "panel_opened_but_no_items", "selectors_not_matched"],
)
def test_parse_failure_exposes_distinct_reason(settings: Any, reason: str) -> None:
    observation = make_observation(
        [], state="unknown", container_found=False, exhausted=True
    )
    observation["reason"] = reason
    service, _ = build_service(settings, {VIDEO_ID: observation})
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "parse_failed"
    assert result["error_code"] == "DOM_MISMATCH"
    assert result["reason"] == reason
    assert any(reason in warning for warning in result["warnings"])


def test_risk_control_aborts_without_retry(settings: Any) -> None:
    service, executor = build_service(
        settings,
        {VIDEO_ID: make_observation([], state="risk_control", exhausted=False)},
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "aborted_risk_control"
    assert result["error_code"] == "RISK_CONTROL"
    assert result["comments"] == []
    assert result["has_more"] is False
    assert len(executor.commands) == 1
    assert "验证" in result["message"]


def test_limit_truncation_sets_sampled_true(settings: Any) -> None:
    comments = [make_comment("1", "第一条"), make_comment("2", "第二条")]
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments,
                total_comment_count=None,
                exhausted=False,
                raw_item_count=2,
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID, limit=2)

    assert result["sampled"] is True
    assert result["completeness"] == "truncated"
    assert result["has_more"] is True
    assert result["next_cursor"] == "offset:2"


def test_pagination_bottom_has_no_more(settings: Any) -> None:
    comments = [make_comment("1", "第一条")]
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments, total_comment_count=None, exhausted=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["has_more"] is False
    assert result["next_cursor"] is None


def test_unknown_total_without_truncation_is_null_unknown(settings: Any) -> None:
    comments = [make_comment("1", "第一条")]
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments, total_comment_count=None, exhausted=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["sampled"] is None
    assert result["completeness"] == "unknown"


def test_known_total_lower_than_returned_is_complete(settings: Any) -> None:
    comments = [make_comment("1", "第一条"), make_comment("2", "第二条")]
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments, total_comment_count=2, exhausted=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["sampled"] is False
    assert result["completeness"] == "complete"


def test_keyword_filter_counts_hits_and_keeps_cursor(settings: Any) -> None:
    comments = [
        make_comment("1", "求个链接"),
        make_comment("2", "太好了"),
        make_comment("3", "链接发我"),
    ]
    service, executor = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments, total_comment_count=None, exhausted=False
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID, keyword="链接")

    assert result["total"] == 2
    assert [comment["comment_id"] for comment in result["comments"]] == ["1", "3"]
    assert result["next_cursor"] is not None
    assert result["has_more"] is True
    # Keyword scans a wider window than the final limit (business logic in service).
    assert executor.commands[0].limit > 50


def test_daily_guard_returns_completed_rate_limited(settings: Any) -> None:
    comments = [make_comment("1", "求链接")]
    service, executor = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                comments, total_comment_count=1, exhausted=True
            ),
            OTHER_VIDEO_ID: make_observation(
                [make_comment("9", "另一条作品")],
                total_comment_count=1,
                exhausted=True,
            ),
        },
    )

    first = service.get_video_comments(VIDEO_ID)
    second = service.get_video_comments(VIDEO_ID)
    other = service.get_video_comments(OTHER_VIDEO_ID)

    assert first["status"] == "completed"
    assert first["rate_limited"] is False
    assert second["status"] == "completed"
    assert second["rate_limited"] is True
    assert second["comments"] == []
    assert second["sampled"] is None
    assert second["completeness"] == "unknown"
    assert "每天最多" in second["message"]
    # The second call must not touch the browser again.
    assert len(executor.commands) == 2
    assert other["rate_limited"] is False
    assert other["total"] == 1


def test_guard_is_keyed_by_video_and_day(settings: Any) -> None:
    guard_path = settings.data_dir / "comment_collection_guard.json"
    comments = [make_comment("1", "求链接")]
    service, _ = build_service(
        settings,
        {VIDEO_ID: make_observation(comments, total_comment_count=1)},
    )
    service.get_video_comments(VIDEO_ID)
    payload = json.loads(guard_path.read_text(encoding="utf-8"))

    assert payload["entries"][VIDEO_ID]["date"] == "2026-11-15"
    assert "comments" not in payload["entries"][VIDEO_ID]


def test_response_has_no_sensitive_fields(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [make_comment("1", "求链接", nickname="测试昵称乙")],
                total_comment_count=1,
                exhausted=True,
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)
    serialized = json.dumps(result, ensure_ascii=False).lower()

    forbidden_keys = {
        "cookie",
        "cookies",
        "headers",
        "authorization",
        "user_id",
        "uid",
        "avatar",
        "avatar_url",
        "home_url",
        "profile_url",
    }
    assert not _walk_keys(result) & forbidden_keys
    for needle in ("cookie", "avatar", "sec_user", "user_id"):
        assert needle not in serialized
    assert "测试昵称乙" in serialized


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def test_invalid_limit_and_cursor_raise_validation_error(settings: Any) -> None:
    from douyin_creator_mcp.errors import AppError

    service, _ = build_service(settings, {})

    with pytest.raises(AppError):
        service.get_video_comments(VIDEO_ID, limit=0)
    with pytest.raises(AppError):
        service.get_video_comments(VIDEO_ID, limit=201)
    with pytest.raises(AppError):
        service.get_video_comments(VIDEO_ID, cursor="bogus-cursor")


def test_custom_keyword_rules_and_version(settings: Any) -> None:
    rules_path = settings.data_dir / "comment_keywords.json"
    rules_path.write_text(
        json.dumps(
            {
                "version": "comment-keywords-test-v9",
                "categories": {"测试类": ["测试词"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [make_comment("1", "这里包含测试词")],
                total_comment_count=1,
                exhausted=True,
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["insights"]["keyword_rule_version"] == "comment-keywords-test-v9"
    assert result["insights"]["categories"] == {"测试类": 1}


def test_empty_without_numeric_total_is_complete(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [], state="empty", total_comment_count=None, exhausted=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "completed"
    assert result["total"] == 0
    assert result["sampled"] is False
    assert result["completeness"] == "complete"


def test_terminal_statuses_always_expose_total_comment_count(settings: Any) -> None:
    for state, expected in (
        ("risk_control", "aborted_risk_control"),
        ("login_required", "user_action_required"),
        ("unknown", "parse_failed"),
    ):
        service, _ = build_service(
            settings,
            {VIDEO_ID: make_observation([], state=state, exhausted=False)},
        )
        result = service.get_video_comments(VIDEO_ID)
        assert result["status"] == expected
        assert "total_comment_count" in result
        assert result["total_comment_count"] is None


def test_cursor_continuation_is_blocked_by_daily_guard(settings: Any) -> None:
    # Contract tension: v1 has no comment persistence, so a continuation call
    # re-opens the panel and is blocked by the per-video daily guard.
    service, executor = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [make_comment("1", "第一条")],
                total_comment_count=None,
                exhausted=False,
            )
        },
    )
    first = service.get_video_comments(VIDEO_ID, limit=1)
    assert first["next_cursor"] == "offset:1"
    second = service.get_video_comments(
        VIDEO_ID, limit=1, cursor=first["next_cursor"]
    )
    assert second["status"] == "completed"
    assert second["rate_limited"] is True
    assert len(executor.commands) == 1


def test_recent_mode_merges_videos_and_does_not_fake_totals(settings: Any) -> None:
    from douyin_creator_mcp.storage.db import Database

    db = Database(settings.data_dir / "douyin.sqlite")
    db.init_schema()
    for index, video_id in enumerate((OTHER_VIDEO_ID, VIDEO_ID), start=1):
        db.execute(
            "INSERT INTO videos (id, account_id, video_id, publish_time, source, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"local-{index}",
                "browser-default",
                video_id,
                1_700_000_000 + index,
                "browser_list",
                "2026-11-15T00:00:00+08:00",
                "2026-11-15T00:00:00+08:00",
            ),
        )
    executor = FakeExecutor(
        {
            VIDEO_ID: make_observation(
                [make_comment("1", "求链接")],
                total_comment_count=5,
                exhausted=True,
            ),
            OTHER_VIDEO_ID: make_observation(
                [make_comment("2", "谢谢")],
                total_comment_count=3,
                exhausted=True,
            ),
        }
    )
    service = CommentsService(
        settings,
        db=db,
        browser_executor=executor,
        now=lambda: NOW,
    )

    result = service.get_video_comments("recent", recent=2)

    assert result["status"] == "completed"
    assert result["video_id"] == "recent"
    assert result["total"] == 2
    assert result["total_comment_count"] is None
    assert [item["video_id"] for item in result["comments"]] == [
        VIDEO_ID,
        OTHER_VIDEO_ID,
    ]
    assert {summary["video_id"] for summary in result["videos"]} == {
        VIDEO_ID,
        OTHER_VIDEO_ID,
    }
    # Each video declared more comments than returned, so the merged recent
    # result is a sample even though no cross-video total is fabricated.
    assert result["sampled"] is True
    assert result["completeness"] == "truncated"


def test_items_rendered_but_all_unparseable_is_parse_failed(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [],
                state="comments",
                total_comment_count=None,
                dropped_item_count=3,
                raw_item_count=0,
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "parse_failed"
    assert result["error_code"] == "DOM_MISMATCH"
    assert result["comments"] == []


def test_comments_disabled_is_not_completed_zero(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [],
                state="comments_disabled",
                total_comment_count=None,
                panel_found=False,
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "parse_failed"
    assert result["error_code"] == "COMMENTS_DISABLED"
    assert result["reason"] == "comments_disabled"
    assert result["comments"] == []
    assert "关闭" in result["message"]


def test_declared_total_with_zero_items_is_parse_failed(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [], total_comment_count=51, exhausted=True, panel_found=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "parse_failed"
    assert result["error_code"] == "DOM_MISMATCH"
    assert result["total"] == 0
    assert result["total_comment_count"] == 51
    assert any("51" in warning for warning in result["warnings"])


def test_true_empty_state_is_completed_zero(settings: Any) -> None:
    service, _ = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [], state="empty", total_comment_count=0, exhausted=True
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "completed"
    assert result["comments"] == []
    assert result["total"] == 0
    assert result["sampled"] is False
    assert result["completeness"] == "complete"


class FlakyNavigationExecutor:
    """Raise navigation/context errors for the first N calls, then succeed."""

    def __init__(self, observation: dict[str, Any], failures: int) -> None:
        self.observation = observation
        self.failures = failures
        self.commands: list[Any] = []

    def execute(self, command: Any) -> dict[str, Any]:
        self.commands.append(command)
        if len(self.commands) <= self.failures:
            raise RuntimeError(
                "Execution context was destroyed, most likely because of a navigation"
            )
        return self.observation


def _service_with_executor(settings: Any, executor: Any, sleeps: list[float]):
    from douyin_creator_mcp.services.comments import CommentsService

    return CommentsService(
        settings,
        db=None,
        browser_executor=executor,
        now=lambda: NOW,
        retry_delay=(1.25, 1.25),
        sleep=sleeps.append,
    )


def test_navigation_race_retries_once_with_configured_delay(settings: Any) -> None:
    observation = make_observation(
        [make_comment("1", "原文呢，求发出来")],
        total_comment_count=1,
        exhausted=True,
    )
    executor = FlakyNavigationExecutor(observation, failures=1)
    sleeps: list[float] = []
    service = _service_with_executor(settings, executor, sleeps)

    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "completed"
    assert len(executor.commands) == 2
    assert sleeps == [1.25]


def test_navigation_race_second_failure_stays_retryable(settings: Any) -> None:
    from douyin_creator_mcp.errors import AppError

    executor = FlakyNavigationExecutor(make_observation(), failures=2)
    sleeps: list[float] = []
    service = _service_with_executor(settings, executor, sleeps)

    with pytest.raises(AppError) as excinfo:
        service.get_video_comments(VIDEO_ID)

    assert excinfo.value.error_type == "navigation_race"
    assert excinfo.value.retryable is True
    assert len(executor.commands) == 2
    assert sleeps == [1.25]


def test_risk_control_does_not_trigger_navigation_retry(settings: Any) -> None:
    service, executor = build_service(
        settings,
        {
            VIDEO_ID: make_observation(
                [], state="risk_control", total_comment_count=None
            )
        },
    )
    result = service.get_video_comments(VIDEO_ID)

    assert result["status"] == "aborted_risk_control"
    assert len(executor.commands) == 1
