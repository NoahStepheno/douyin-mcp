"""Issue #6: account-level risk cooldown, skipped counters, diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.cli import main as cli_main
from douyin_creator_mcp.config import load_settings
from douyin_creator_mcp.errors import AppError
from douyin_creator_mcp.services.browser_service import BrowserService
from douyin_creator_mcp.services.comments import CommentsService
from douyin_creator_mcp.services.risk_state import (
    RiskCooldown,
    SkippedEventLog,
)

from .fixtures import FakeExecutor, make_comment, make_observation

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=SHANGHAI)
VIDEO_ID = "7707861402739952923"
OTHER_VIDEO_ID = "7000000000000000002"


class Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs: Any) -> None:
        self.moment += timedelta(**kwargs)


class FakeDB:
    def query_one(self, sql: str, params: Any = (), **kwargs: Any) -> None:
        return None

    def query_all(self, sql: str, params: Any = (), **kwargs: Any) -> list[Any]:
        return []


def _service(
    settings: Any,
    observations: dict[str, Any],
    clock: Clock,
) -> tuple[CommentsService, FakeExecutor]:
    executor = FakeExecutor(observations)
    service = CommentsService(
        settings,
        db=None,
        browser_executor=executor,
        now=clock,
        risk_cooldown=RiskCooldown(settings.data_dir, now=clock),
        skipped_log=SkippedEventLog(settings.data_dir, now=clock),
    )
    return service, executor


def _completed_observation(comment_id: str = "1") -> dict[str, Any]:
    return make_observation(
        [make_comment(comment_id, "原文呢，求发出来")],
        total_comment_count=1,
        exhausted=True,
    )


def test_risk_control_activates_cooldown_and_blocks_without_browser(
    settings: Any,
) -> None:
    clock = Clock()
    service, executor = _service(
        settings,
        {
            VIDEO_ID: make_observation(
                [], state="risk_control", total_comment_count=None
            )
        },
        clock,
    )

    first = service.get_video_comments(VIDEO_ID)
    assert first["status"] == "aborted_risk_control"

    state = RiskCooldown(settings.data_dir, now=clock).status()
    assert state["active"] is True
    assert state["cooldown_until"] == "2026-11-16T00:00:00+08:00"
    assert datetime.fromisoformat(state["cooldown_until"]).tzinfo is not None

    second = service.get_video_comments(OTHER_VIDEO_ID)

    assert second["status"] == "skipped"
    assert second["reason"] == "risk_control_cooldown"
    assert second["error_code"] == "RISK_CONTROL_COOLDOWN"
    assert second["retryable"] is False
    assert second["cooldown_until"] == "2026-11-16T00:00:00+08:00"
    assert second["remaining_seconds"] == 12 * 3600
    assert isinstance(second["remaining_seconds"], int)
    assert second["comments"] == []
    # The second call must not start the browser/executor.
    assert len(executor.commands) == 1


def test_cooldown_expiry_restores_collection(settings: Any) -> None:
    clock = Clock()
    service, executor = _service(
        settings,
        {
            VIDEO_ID: make_observation([], state="risk_control"),
            OTHER_VIDEO_ID: _completed_observation(),
        },
        clock,
    )
    service.get_video_comments(VIDEO_ID)
    assert RiskCooldown(settings.data_dir, now=clock).status()["active"] is True

    clock.advance(hours=13)
    result = service.get_video_comments(OTHER_VIDEO_ID)

    assert result["status"] == "completed"
    assert len(executor.commands) == 2
    assert RiskCooldown(settings.data_dir, now=clock).status()["active"] is False


def test_remaining_seconds_decreases_and_is_integer(settings: Any) -> None:
    clock = Clock()
    cooldown = RiskCooldown(settings.data_dir, now=clock)
    cooldown.activate(12)
    clock.advance(hours=1, seconds=30)
    state = cooldown.status()

    assert state["active"] is True
    assert state["remaining_seconds"] == 11 * 3600 - 30
    assert isinstance(state["remaining_seconds"], int)


def test_read_only_get_status_is_exempt_but_reports_cooldown(
    settings: Any,
) -> None:
    clock = Clock()
    cooldown = RiskCooldown(settings.data_dir, now=clock)
    log = SkippedEventLog(settings.data_dir, now=clock)
    cooldown.activate(12)
    log.record("risk_control_cooldown")
    service = BrowserService(
        settings,
        FakeDB(),  # type: ignore[arg-type]
        risk_cooldown=cooldown,
        skipped_log=log,
    )

    status = service.get_status()

    assert status["status"] == "completed"
    assert status["risk_cooldown"]["active"] is True
    assert status["risk_cooldown"]["remaining_seconds"] == 12 * 3600
    assert status["risk_cooldown"]["state_path"].endswith("risk_cooldown.json")
    assert "schema" in status["risk_cooldown"]
    assert status["skipped_last_7d"] == {"risk_control_cooldown": 1}


def test_skipped_last_7d_counts_both_reasons_and_prunes_old(
    settings: Any,
) -> None:
    clock = Clock()
    log = SkippedEventLog(settings.data_dir, now=clock)
    # Old event outside the 7-day window must be pruned and never counted.
    old = {
        "version": 1,
        "events": [
            {
                "reason": "video_not_public",
                "at": (NOW - timedelta(days=8)).isoformat(),
            }
        ],
    }
    log.path.write_text(json.dumps(old), encoding="utf-8")
    log.record("risk_control_cooldown")
    log.record("risk_control_cooldown")
    log.record("video_not_public")
    log.record("risk_control_cooldown")

    counts = log.counts_last_7d()
    assert counts == {
        "risk_control_cooldown": 3,
        "video_not_public": 1,
    }

    # get_status exposes the same counters (with both reasons present).
    service = BrowserService(
        settings,
        FakeDB(),  # type: ignore[arg-type]
        risk_cooldown=RiskCooldown(settings.data_dir, now=clock),
        skipped_log=log,
    )
    assert service.get_status()["skipped_last_7d"] == {
        "risk_control_cooldown": 3,
        "video_not_public": 1,
    }


def test_manual_cooldown_injection_is_honored(settings: Any) -> None:
    """Acceptance can hand-write the state file instead of burning quota."""

    path = settings.data_dir / "risk_cooldown.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "browser-default": {
                        "cooldown_until": "2026-11-15T20:00:00+08:00",
                        "hours": 6,
                        "created_at": "2026-11-15T14:00:00+08:00",
                        "reason": "risk_control",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    clock = Clock()
    service, executor = _service(
        settings, {OTHER_VIDEO_ID: _completed_observation()}, clock
    )

    result = service.get_video_comments(OTHER_VIDEO_ID)

    assert result["status"] == "skipped"
    assert result["reason"] == "risk_control_cooldown"
    assert result["remaining_seconds"] == 8 * 3600
    assert executor.commands == []


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "entries": {"browser-default": {}}}),
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "browser-default": {
                        "cooldown_until": "2026-11-15 20:00:00",
                        "hours": 12,
                        "reason": "risk_control",
                    }
                },
            }
        ),
    ],
)
def test_invalid_cooldown_file_is_inactive_with_warning(
    settings: Any, payload: str
) -> None:
    (settings.data_dir / "risk_cooldown.json").write_text(payload, encoding="utf-8")
    cooldown = RiskCooldown(settings.data_dir, now=Clock())
    state = cooldown.status()

    assert state["active"] is False
    assert state["warnings"]
    service, executor = _service(
        settings, {OTHER_VIDEO_ID: _completed_observation()}, Clock()
    )
    assert service.get_video_comments(OTHER_VIDEO_ID)["status"] == "completed"
    assert len(executor.commands) == 1


def test_expired_cooldown_is_inactive_with_warning(settings: Any) -> None:
    (settings.data_dir / "risk_cooldown.json").write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "browser-default": {
                        "cooldown_until": "2026-11-15T10:00:00+08:00",
                        "hours": 12,
                        "created_at": "2026-11-14T22:00:00+08:00",
                        "reason": "risk_control",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = RiskCooldown(settings.data_dir, now=Clock()).status()

    assert state["active"] is False
    assert any("过期" in warning for warning in state["warnings"])


def test_cooldown_is_not_cleared_or_extended_by_collection(
    settings: Any,
) -> None:
    clock = Clock()
    service, executor = _service(
        settings,
        {VIDEO_ID: make_observation([], state="risk_control")},
        clock,
    )
    service.get_video_comments(VIDEO_ID)
    until = RiskCooldown(settings.data_dir, now=clock).status()["cooldown_until"]

    for _ in range(3):
        skipped = service.get_video_comments(OTHER_VIDEO_ID)
        assert skipped["status"] == "skipped"
        assert skipped["cooldown_until"] == until
    assert len(executor.commands) == 1
    assert (settings.data_dir / "risk_cooldown.json").is_file()


def test_clear_cooldown_cli_requires_yes_and_clears_manual_state(
    settings: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    RiskCooldown(settings.data_dir).activate(6)
    path = settings.data_dir / "risk_cooldown.json"

    rc_without_yes = cli_main(["clear-cooldown"])
    assert rc_without_yes == 1
    assert path.is_file()
    capsys.readouterr()

    rc = cli_main(["clear-cooldown", "--yes"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["cleared"] is True
    assert path.is_file() is False


def test_clear_cooldown_removes_hand_written_state_too(
    settings: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    path = settings.data_dir / "risk_cooldown.json"
    path.write_text("{hand written but invalid", encoding="utf-8")

    rc = cli_main(["clear-cooldown", "--yes"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["cleared"] is True
    assert path.exists() is False


@pytest.mark.parametrize("hours", [6, 12, 24])
def test_cooldown_hours_config_accepts_allowed_values(hours: int) -> None:
    settings = load_settings(env={"DOUYIN_RISK_COOLDOWN_HOURS": str(hours)})
    assert settings.douyin_risk_cooldown_hours == hours


def test_cooldown_hours_config_defaults_to_12_and_rejects_other_values() -> None:
    assert load_settings(env={}).douyin_risk_cooldown_hours == 12
    with pytest.raises(AppError):
        load_settings(env={"DOUYIN_RISK_COOLDOWN_HOURS": "8"})


def test_final_url_warning_is_sanitized_without_query_values(
    settings: Any,
) -> None:
    observation = _completed_observation()
    observation["final_url"] = (
        "https://www.douyin.com/jingxuan?modal_id=SECRET123&previous_page=feed"
    )
    service, _ = _service(settings, {VIDEO_ID: observation}, Clock())
    result = service.get_video_comments(VIDEO_ID)

    warnings = " ".join(result["warnings"])
    assert "final_url=/jingxuan?modal_id&previous_page" in warnings
    assert "SECRET123" not in warnings
    assert "feed" not in warnings


def test_final_url_warning_skipped_for_same_url_and_known_404(
    settings: Any,
) -> None:
    same = _completed_observation()
    same["final_url"] = (
        f"https://www.douyin.com/video/{VIDEO_ID}"
    )
    service, _ = _service(settings, {VIDEO_ID: same}, Clock())
    assert not any(
        "final_url=" in warning
        for warning in service.get_video_comments(VIDEO_ID)["warnings"]
    )

    not_public = make_observation([], state="video_not_public")
    not_public["final_url"] = (
        "https://www.douyin.com/jingxuan?previous_page=web_video_404_link"
    )
    service, _ = _service(settings, {OTHER_VIDEO_ID: not_public}, Clock())
    result = service.get_video_comments(OTHER_VIDEO_ID)
    assert result["status"] == "skipped"
    assert result["warnings"] == []


def test_cooldown_and_video_not_public_skips_are_counted_in_status(
    settings: Any,
) -> None:
    clock = Clock()
    service, _ = _service(
        settings,
        {
            VIDEO_ID: make_observation([], state="risk_control"),
            OTHER_VIDEO_ID: make_observation([], state="video_not_public"),
        },
        clock,
    )
    # #3 skip first, then a risk hit, then a cooldown skip.
    service.get_video_comments(OTHER_VIDEO_ID)
    service.get_video_comments(VIDEO_ID)
    service.get_video_comments(OTHER_VIDEO_ID)

    status = BrowserService(
        settings,
        FakeDB(),  # type: ignore[arg-type]
        risk_cooldown=RiskCooldown(settings.data_dir, now=clock),
        skipped_log=SkippedEventLog(settings.data_dir, now=clock),
    ).get_status()

    assert status["skipped_last_7d"] == {
        "risk_control_cooldown": 1,
        "video_not_public": 1,
    }


def test_recent_mode_is_blocked_by_cooldown_without_database(
    settings: Any,
) -> None:
    clock = Clock()
    RiskCooldown(settings.data_dir, now=clock).activate(6)
    service, executor = _service(settings, {}, clock)

    result = service.get_video_comments("recent", recent=2)

    assert result["status"] == "skipped"
    assert result["reason"] == "risk_control_cooldown"
    assert executor.commands == []


def test_login_status_is_read_only_exempt_during_cooldown(
    settings: Any,
) -> None:
    clock = Clock()
    cooldown = RiskCooldown(settings.data_dir, now=clock)
    cooldown.activate(12)
    service = BrowserService(
        settings,
        FakeDB(),  # type: ignore[arg-type]
        risk_cooldown=cooldown,
    )

    status = service.login_status()

    assert status["browser_running"] is False
    assert status["login_status"] == "not_started"


def test_sync_collection_is_blocked_by_cooldown_without_browser(
    settings: Any,
) -> None:
    clock = Clock()
    cooldown = RiskCooldown(settings.data_dir, now=clock)
    cooldown.activate(12)
    service = BrowserService(
        settings,
        FakeDB(),  # type: ignore[arg-type]
        risk_cooldown=cooldown,
        skipped_log=SkippedEventLog(settings.data_dir, now=clock),
    )

    list_result = service.sync_creator_data()
    details_result = service.sync_video_details()

    for result in (list_result, details_result):
        assert result["status"] == "skipped"
        assert result["reason"] == "risk_control_cooldown"
        assert result["retryable"] is False
    # No browser session/executor was created by either call.
    assert service._session is None
    assert service._browser_executor is None
