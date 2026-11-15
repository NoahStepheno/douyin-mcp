"""Offline tests for douyin_browser_get_account_overview (issue #4)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.browser.account_overview import (
    ACCOUNT_OVERVIEW_PARSER_VERSION,
    STATE_LOGIN_REQUIRED,
    STATE_NOT_RENDERED,
    STATE_RISK_CONTROL,
    decide_state,
    read_account_overview,
    select_period,
    wait_for_account_overview,
)
from douyin_creator_mcp.services.account_overview import (
    FOLLOWER_INDICATOR_WARNING,
    PERIOD_MISSING_WARNING,
    AccountOverviewService,
)
from douyin_creator_mcp.services.risk_state import RiskCooldown

from .fixtures_account_overview import (
    LABEL_CHANGED,
    MISSING_FIELDS,
    PERIOD_MISSING,
    REAL_30D,
    REAL_7D,
    SHELL,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 15, 13, 30, 0, tzinfo=SHANGHAI)


class OverviewExecutor:
    def __init__(self, observation: dict[str, Any]) -> None:
        self.observation = observation
        self.commands: list[Any] = []

    def execute(self, command: Any) -> dict[str, Any]:
        self.commands.append(command)
        return self.observation


def _observation(raw: dict[str, Any], period_switch: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "state": decide_state(raw),
        "final_url": raw.get("url"),
        "actual_period_label": raw.get("current_period_label"),
        "period_start": raw.get("period_start"),
        "period_end": raw.get("period_end"),
        "metrics": raw.get("metrics") or {},
        "like_total": raw.get("like_total"),
        "follower_indicator": raw.get("follower_indicator"),
        "period_switch": period_switch,
        "waited_ms": raw.get("waited_ms"),
    }


def _service(settings: Any, observation: dict[str, Any]) -> tuple[AccountOverviewService, OverviewExecutor]:
    executor = OverviewExecutor(observation)
    service = AccountOverviewService(
        settings,
        browser_executor=executor,
        now=lambda: NOW,
        risk_cooldown=RiskCooldown(settings.data_dir, now=lambda: NOW),
    )
    return service, executor


def _render(page: Any, html: str) -> dict[str, Any]:
    page.set_content(html, wait_until="domcontentloaded")
    return read_account_overview(page)


def test_normal_7d_parse(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, REAL_7D)
    assert decide_state(raw) == "rendered"
    service, _ = _service(settings, _observation(raw))

    result = service.get_account_overview("7d")

    assert result["status"] == "completed"
    assert result["period"] == "7d"
    assert result["period_start"] == "2026-11-08"
    assert result["period_end"] == "2026-11-14"
    assert result["captured_at"] == "2026-11-15T13:30:00+08:00"
    assert result["parser_version"] == ACCOUNT_OVERVIEW_PARSER_VERSION
    assert result["account_play_count"] == {"value": 901, "missing_reason": None}
    assert result["account_play_delta"] == {"value": 897, "missing_reason": None}
    assert result["profile_visit_count_period"] == {"value": 24, "missing_reason": None}
    assert result["profile_visit_delta"] == {"value": 24, "missing_reason": None}
    assert result["like_count"]["value"] == 34
    assert result["share_count"]["value"] == 1
    assert result["comment_count"]["value"] == 3
    assert result["net_follower_gain"]["value"] == 1
    assert result["like_total"] == {"value": 185, "missing_reason": None}
    assert result["follower_indicator"] == {
        "value": 128,
        "label_text": "粉丝指数",
        "missing_reason": None,
    }
    assert "粉丝总数" not in json.dumps(result, ensure_ascii=False)


def test_30d_parse_uses_period_text_and_values(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, REAL_30D)
    assert raw["current_period_label"] == "近30日"
    service, _ = _service(settings, _observation(raw))

    result = service.get_account_overview("30d")

    assert result["status"] == "completed"
    assert result["period_start"] == "2026-10-16"
    assert result["period_end"] == "2026-11-14"
    assert result["account_play_count"]["value"] == 913
    assert result["profile_visit_count_period"]["value"] == 26
    assert result["net_follower_gain"]["value"] == 0
    # 30d panel has no 较前 comparison text.
    assert result["account_play_delta"] == {"value": None, "missing_reason": "not_displayed"}
    assert result["profile_visit_delta"] == {"value": None, "missing_reason": "not_displayed"}


def test_7d_missing_period_text_is_tolerated(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, PERIOD_MISSING)
    assert raw["period_start"] is None
    service, _ = _service(settings, _observation(raw))

    result = service.get_account_overview("7d")

    assert result["status"] == "completed"
    assert result["period_start"] is None
    assert result["period_end"] is None
    assert result["account_play_count"]["value"] == 901
    assert PERIOD_MISSING_WARNING in result["warnings"]


def test_950_trap_is_never_account_play_count(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, REAL_7D)
    assert "950" in dom_page.inner_text("body")  # captured 最新作品 block
    service, _ = _service(settings, _observation(raw))

    result = service.get_account_overview("7d")

    assert result["account_play_count"]["value"] == 901
    for field in ("account_play_count", "account_play_delta"):
        assert result[field]["value"] != 950
    assert raw["metrics"]["account_play_count"]["value"] == 901


def test_missing_fields_get_individual_reasons(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, MISSING_FIELDS)
    service, _ = _service(settings, _observation(raw))

    result = service.get_account_overview("7d")

    assert result["status"] == "completed"
    assert result["net_follower_gain"] == {"value": None, "missing_reason": "not_displayed"}
    assert result["like_total"] == {"value": None, "missing_reason": "not_displayed"}
    # Other fields remain parsed.
    assert result["account_play_count"]["value"] == 901
    assert result["like_count"]["value"] == 34


def test_lazy_render_not_ready_is_parse_failed(dom_page: Any, settings: Any) -> None:
    dom_page.set_content(SHELL)
    waited = wait_for_account_overview(dom_page, settle_ms=10, timeout_ms=40, poll_ms=10)
    assert decide_state(waited) == STATE_NOT_RENDERED

    service, _ = _service(settings, _observation(waited))
    result = service.get_account_overview("7d")

    assert result["status"] == "parse_failed"
    assert result["error_code"] == "DOM_MISMATCH"
    assert result["reason"] == "account_overview_not_rendered"
    assert result["account_play_count"]["value"] is None


def test_login_wall_returns_user_action_required(settings: Any) -> None:
    service, _ = _service(
        settings,
        {
            "state": STATE_LOGIN_REQUIRED,
            "metrics": {},
            "period_switch": None,
        },
    )
    result = service.get_account_overview("7d")

    assert result["status"] == "user_action_required"
    assert result["error_code"] == "LOGIN_REQUIRED"
    assert result["account_play_count"]["value"] is None


def test_risk_control_aborts_and_activates_shared_cooldown(settings: Any) -> None:
    service, executor = _service(
        settings,
        {"state": STATE_RISK_CONTROL, "metrics": {}, "period_switch": None},
    )

    result = service.get_account_overview("7d")

    assert result["status"] == "aborted_risk_control"
    assert result["error_code"] == "RISK_CONTROL"
    assert len(executor.commands) == 1
    cooldown = RiskCooldown(settings.data_dir, now=lambda: NOW).status()
    assert cooldown["active"] is True
    assert cooldown["blocked_scope"] == "comments_only"


def test_period_not_switchable_is_parse_failed(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, REAL_7D)
    switch = select_period(dom_page, "30d", timeout_ms=100, poll_ms=10)
    assert switch["selected"] is False
    service, _ = _service(settings, _observation(raw, period_switch=switch))

    result = service.get_account_overview("30d")

    assert result["status"] == "parse_failed"
    assert result["reason"] == "period_not_switchable"
    assert result["account_play_count"]["value"] is None


def test_daily_guard_is_per_account_period(settings: Any) -> None:
    observation = {
        "state": "rendered",
        "period_start": "2026-11-08",
        "period_end": "2026-11-14",
        "metrics": {"account_play_count": {"value": 901, "delta": 897}},
        "like_total": 185,
        "follower_indicator": {"value": 128, "label_text": "粉丝指数"},
        "period_switch": None,
    }
    service, executor = _service(settings, observation)

    first = service.get_account_overview("7d")
    second = service.get_account_overview("7d")
    other_period = service.get_account_overview("30d")

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["rate_limited"] is True
    assert second["account_play_count"] == {"value": None, "missing_reason": "rate_limited"}
    assert len(executor.commands) == 2
    assert other_period["rate_limited"] is False


def test_like_total_uses_text_anchor_and_number_sibling(dom_page: Any) -> None:
    raw = _render(dom_page, REAL_7D)
    assert raw["like_total"] == 185

    missing = _render(dom_page, MISSING_FIELDS)
    assert missing["like_total"] is None


def test_follower_indicator_keeps_platform_label_and_warns(
    dom_page: Any, settings: Any
) -> None:
    raw = _render(dom_page, REAL_7D)
    service, _ = _service(settings, _observation(raw))
    result = service.get_account_overview("7d")

    assert result["follower_indicator"]["label_text"] == "粉丝指数"
    assert result["follower_indicator"]["value"] == 128
    assert FOLLOWER_INDICATOR_WARNING in result["warnings"]

    (settings.data_dir / "account_overview_guard.json").unlink(missing_ok=True)
    changed = _render(dom_page, LABEL_CHANGED)
    service, _ = _service(settings, _observation(changed))
    changed_result = service.get_account_overview("7d")
    assert changed_result["follower_indicator"]["label_text"] == "粉丝数"


def test_response_has_no_sensitive_fields(dom_page: Any, settings: Any) -> None:
    raw = _render(dom_page, REAL_7D)
    service, _ = _service(settings, _observation(raw))
    result = service.get_account_overview("7d")
    serialized = json.dumps(result, ensure_ascii=False).lower()

    for needle in (
        "cookie",
        "avatar",
        "sec_uid",
        "uid",
        "抖音号",
        "603879564",
        "活着的小贝壳",
        "http://",
        "https://",
    ):
        assert needle not in serialized
    assert "粉丝总数" not in serialized

    fixture_text = REAL_7D
    for needle in ("活着的小贝壳", "603879564", "MS4wLjAB"):
        assert needle not in fixture_text


def test_invalid_period_rejected(settings: Any) -> None:
    from douyin_creator_mcp.errors import AppError

    service, _ = _service(settings, {})
    with pytest.raises(AppError):
        service.get_account_overview("all")
