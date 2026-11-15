"""Business logic for the account overview tool (issue #4)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..browser.account_overview import (
    ACCOUNT_OVERVIEW_PARSER_VERSION,
    PERIOD_LABELS,
    PERIODS,
    STATE_LOGIN_REQUIRED,
    STATE_NOT_RENDERED,
    STATE_RISK_CONTROL,
)
from ..browser.commands import CollectAccountOverview
from ..errors import DATA_NOT_AVAILABLE, VALIDATION_ERROR, AppError
from .browser_service import BROWSER_DEFAULT_ACCOUNT_ID
from .risk_state import RiskCooldown

SHANGHAI = ZoneInfo("Asia/Shanghai")
OVERVIEW_GUARD_FILENAME = "account_overview_guard.json"
OVERVIEW_GUARD_VERSION = 1

STATUS_COMPLETED = "completed"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_ABORTED_RISK_CONTROL = "aborted_risk_control"
STATUS_USER_ACTION_REQUIRED = "user_action_required"

MISSING_NOT_DISPLAYED = "not_displayed"
MISSING_NOT_COLLECTED = "not_collected"
MISSING_RATE_LIMITED = "rate_limited"

FOLLOWER_INDICATOR_WARNING = (
    "平台标签为「粉丝指数」，语义未确认，不可当作粉丝数量。"
)
PERIOD_MISSING_WARNING = (
    "页面未展示「统计周期」，period_start / period_end 为 null。"
)
NOT_RENDERED_MESSAGE = (
    "未等到「账号总览」区块渲染完成（懒加载超时或页面结构变化）。"
)
PERIOD_NOT_SWITCHABLE_MESSAGE = (
    "无法把「时间」选择器切到请求的时间范围，已停止解析以免返回错误口径。"
)

METRIC_FIELDS = (
    ("account_play_count", "account_play_count"),
    ("profile_visit_count_period", "profile_visit_count_period"),
    ("work_like_count_period", "work_like_count_period"),
    ("work_share_count_period", "work_share_count_period"),
    ("work_comment_count_period", "work_comment_count_period"),
    ("net_follower_gain", "net_follower_gain"),
)


class AccountOverviewDailyGuard:
    """Per account + period + local date guard (same pattern as comments)."""

    def __init__(
        self,
        path: Path | str,
        *,
        account_id: str = BROWSER_DEFAULT_ACCOUNT_ID,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.account_id = account_id
        self._now = now or (lambda: datetime.now(SHANGHAI))

    def _key(self, period: str) -> str:
        today = self._now().astimezone(SHANGHAI).date().isoformat()
        return f"{self.account_id}|{period}|{today}"

    def already_collected(self, period: str) -> bool:
        return self._key(period) in self._entries()

    def record(self, period: str, captured_at: str) -> None:
        entries = self._entries()
        entries[self._key(period)] = {"captured_at": captured_at}
        cutoff = (
            self._now().astimezone(SHANGHAI) - timedelta(days=2)
        ).date().isoformat()
        pruned = {
            key: value
            for key, value in entries.items()
            if key.rsplit("|", 1)[-1] >= cutoff
        }
        payload = {"version": OVERVIEW_GUARD_VERSION, "entries": pruned}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _entries(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        entries = payload.get("entries")
        return dict(entries) if isinstance(entries, dict) else {}


class AccountOverviewService:
    def __init__(
        self,
        settings: Any,
        *,
        browser_executor: Any | None = None,
        now: Callable[[], datetime] | None = None,
        guard: AccountOverviewDailyGuard | None = None,
        risk_cooldown: RiskCooldown | None = None,
    ) -> None:
        self.settings = settings
        self._browser_executor = browser_executor
        self._now = now or (lambda: datetime.now(SHANGHAI))
        self._guard = guard or AccountOverviewDailyGuard(
            Path(settings.data_dir) / OVERVIEW_GUARD_FILENAME, now=self._now
        )
        self._risk_cooldown = risk_cooldown or RiskCooldown(
            Path(settings.data_dir), now=self._now
        )

    def get_account_overview(self, period: str = "7d") -> dict[str, Any]:
        period = str(period or "").strip()
        if period not in PERIODS:
            raise AppError(
                VALIDATION_ERROR,
                "period must be one of yesterday, 7d, 30d.",
            )
        captured_at = self._now().astimezone(SHANGHAI).replace(microsecond=0)
        if self._guard.already_collected(period):
            return self._rate_limited_result(period, captured_at)
        observation = self._collect(period, captured_at)
        if observation.get("state") == STATE_RISK_CONTROL:
            self._risk_cooldown.activate(
                int(getattr(self.settings, "douyin_risk_cooldown_hours", 12))
            )
        result = self._build_result(period, observation, captured_at)
        if result["status"] == STATUS_COMPLETED:
            try:
                self._guard.record(period, captured_at.isoformat())
            except OSError:
                result["warnings"].append(
                    "本地每日限频状态写入失败，本次结果有效但未能记账。"
                )
        return result

    def _collect(self, period: str, captured_at: datetime) -> dict[str, Any]:
        if self._browser_executor is None:
            raise AppError(
                DATA_NOT_AVAILABLE, "浏览器执行器不可用，无法读取账号总览。"
            )
        return self._browser_executor.execute(
            CollectAccountOverview(
                period=period,
                settle_ms=int(
                    getattr(self.settings, "douyin_account_overview_settle_ms", 12000)
                ),
                timeout_ms=int(
                    getattr(self.settings, "douyin_account_overview_timeout_ms", 20000)
                ),
                collected_at=captured_at.isoformat(),
            )
        )

    def _build_result(
        self,
        period: str,
        observation: dict[str, Any],
        captured_at: datetime,
    ) -> dict[str, Any]:
        state = str(observation.get("state") or STATE_NOT_RENDERED)
        base = {
            "period": period,
            "captured_at": captured_at.isoformat(),
            "rate_limited": False,
            "requested_period_label": PERIOD_LABELS[period],
            "actual_period_label": observation.get("actual_period_label"),
            "parser_version": ACCOUNT_OVERVIEW_PARSER_VERSION,
        }
        if state == STATE_RISK_CONTROL:
            return {
                **base,
                "status": STATUS_ABORTED_RISK_CONTROL,
                "error_code": "RISK_CONTROL",
                "reason": "risk_control",
                "retryable": False,
                "message": "账号总览页出现验证，已停止采集，请手动处理后再试。",
                **self._empty_fields(MISSING_NOT_COLLECTED),
                "warnings": [],
            }
        if state == STATE_LOGIN_REQUIRED:
            return {
                **base,
                "status": STATUS_USER_ACTION_REQUIRED,
                "error_code": "LOGIN_REQUIRED",
                "reason": "login_required",
                "retryable": False,
                "message": "当前浏览器未登录创作者中心，请先完成登录。",
                **self._empty_fields(MISSING_NOT_COLLECTED),
                "warnings": [],
            }
        period_switch = observation.get("period_switch") or {}
        if state == STATE_NOT_RENDERED:
            return {
                **base,
                "status": STATUS_PARSE_FAILED,
                "error_code": "DOM_MISMATCH",
                "reason": "account_overview_not_rendered",
                "retryable": True,
                "message": NOT_RENDERED_MESSAGE,
                **self._empty_fields(MISSING_NOT_COLLECTED),
                "warnings": [],
            }
        if period_switch and not period_switch.get("selected"):
            return {
                **base,
                "status": STATUS_PARSE_FAILED,
                "error_code": "DOM_MISMATCH",
                "reason": "period_not_switchable",
                "retryable": True,
                "message": PERIOD_NOT_SWITCHABLE_MESSAGE,
                **self._empty_fields(MISSING_NOT_COLLECTED),
                "warnings": [str(period_switch.get("reason") or "")],
            }
        warnings: list[str] = []
        period_start = observation.get("period_start")
        period_end = observation.get("period_end")
        if not period_start or not period_end:
            warnings.append(PERIOD_MISSING_WARNING)
        metrics = observation.get("metrics") or {}
        result: dict[str, Any] = {
            **base,
            "status": STATUS_COMPLETED,
            "error_code": None,
            "reason": None,
            "retryable": False,
            "period_start": period_start,
            "period_end": period_end,
        }
        for field, metric_key in METRIC_FIELDS:
            metric = metrics.get(metric_key) or {}
            result[field] = self._field(metric.get("value"))
        result["account_play_delta"] = self._field(
            (metrics.get("account_play_count") or {}).get("delta")
        )
        result["profile_visit_delta"] = self._field(
            (metrics.get("profile_visit_count_period") or {}).get("delta")
        )
        result["like_total"] = self._field(observation.get("like_total"))
        indicator = observation.get("follower_indicator") or {}
        label_text = indicator.get("label_text")
        result["follower_indicator"] = {
            "value": indicator.get("value"),
            "label_text": label_text,
            "missing_reason": None
            if indicator.get("value") is not None
            else MISSING_NOT_DISPLAYED,
        }
        if label_text == "粉丝指数":
            warnings.append(FOLLOWER_INDICATOR_WARNING)
        result["warnings"] = warnings
        return result

    @staticmethod
    def _field(value: Any, missing_reason: str = MISSING_NOT_DISPLAYED) -> dict[str, Any]:
        return {
            "value": value,
            "missing_reason": None if value is not None else missing_reason,
        }

    def _empty_fields(self, missing_reason: str) -> dict[str, Any]:
        fields: dict[str, Any] = {
            field: self._field(None, missing_reason)
            for field, _ in METRIC_FIELDS
        }
        fields["account_play_delta"] = self._field(None, missing_reason)
        fields["profile_visit_delta"] = self._field(None, missing_reason)
        fields["like_total"] = self._field(None, missing_reason)
        fields["follower_indicator"] = {
            "value": None,
            "label_text": None,
            "missing_reason": missing_reason,
        }
        fields["period_start"] = None
        fields["period_end"] = None
        return fields

    def _rate_limited_result(
        self, period: str, captured_at: datetime
    ) -> dict[str, Any]:
        return {
            "period": period,
            "captured_at": captured_at.isoformat(),
            "requested_period_label": PERIOD_LABELS[period],
            "actual_period_label": None,
            "status": STATUS_COMPLETED,
            "error_code": None,
            "reason": None,
            "retryable": False,
            "rate_limited": True,
            "message": (
                "同一账号 + 时间范围每天最多采集 1 次账号总览；"
                "本次未访问平台，请明天再试。"
            ),
            **self._empty_fields(MISSING_RATE_LIMITED),
            "parser_version": ACCOUNT_OVERVIEW_PARSER_VERSION,
            "warnings": ["命中每日限频，字段值不可用（missing_reason=rate_limited）。"],
        }
