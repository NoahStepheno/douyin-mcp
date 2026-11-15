"""Business logic for the visible comment panel tool (v1).

Layering: DOM reading/parsing lives in ``browser/comments.py``; this module owns
validation, the per-video daily guard, keyword filtering, sampling semantics and
the response contract. No comment is persisted: v1 returns them directly.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..browser.comments import (
    COMMENT_PARSER_VERSION,
    MAX_COMMENT_LIMIT,
    STATE_COMMENTS_DISABLED,
    STATE_EMPTY,
    STATE_VIDEO_NOT_PUBLIC,
    STATE_LOGIN_REQUIRED,
    STATE_RISK_CONTROL,
    STATE_UNKNOWN,
    collect_comments,
    is_not_public_url,
    sanitize_final_url,
)
from ..browser.commands import CollectComments
from ..errors import (
    DATA_NOT_AVAILABLE,
    NAVIGATION_RACE,
    VALIDATION_ERROR,
    AppError,
)
from .browser_service import BROWSER_DEFAULT_ACCOUNT_ID
from .comment_insights import build_insights, load_keyword_rules
from .risk_state import RiskCooldown, SkippedEventLog

SHANGHAI = ZoneInfo("Asia/Shanghai")
COMMENT_GUARD_FILENAME = "comment_collection_guard.json"
COMMENT_GUARD_VERSION = 1
COMMENT_DAILY_LIMIT = 1
#: Retry one navigation race, with a random cool-down to avoid hammering the
#: platform. Tests inject a custom (min, max) and sleep recorder.
NAVIGATION_RETRY_MIN_SECONDS = 2.0
NAVIGATION_RETRY_MAX_SECONDS = 3.0
NAVIGATION_RETRY_ATTEMPTS = 1
RECENT_DEFAULT = 2
RECENT_MAX = 20
CURSOR_PATTERN = re.compile(r"^offset:(\d+)$")
VIDEO_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{4,64}$")

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_ABORTED_RISK_CONTROL = "aborted_risk_control"
STATUS_USER_ACTION_REQUIRED = "user_action_required"

RISK_MESSAGE = "评论页出现验证，已停止采集，请手动处理后再试。"
LOGIN_MESSAGE = "评论页未登录或登录已过期，请在可见浏览器中完成登录后重试。"
PARSE_FAILED_MESSAGE = (
    "未识别到评论区结构（DOM 选择器未命中），已停止解析；"
    "这不等同于 0 条评论。"
)
RATE_LIMITED_MESSAGE = (
    "同一作品每天最多采集 1 次评论（本地频率限制），本次未重新访问评论页。"
)
REASON_MESSAGES = {
    "comments_disabled": (
        "该作品评论区已关闭（作者关闭了评论），没有可采集的评论。"
    ),
    "risk_control_cooldown": (
        "账号处于风控冷却期，本次不访问平台（不消耗请求）；"
        "请等待冷却到期，或人工确认安全后运行 douyin-mcp clear-cooldown --yes。"
    ),
    "video_not_public": (
        "该作品的公开页不存在（已删除、仅自己可见或未公开），"
        "公开数据源不适用；创作者侧评论请在创作者后台查看。"
    ),
    "panel_not_opened": (
        "未能打开评论面板（评论入口未命中或面板未渲染）。"
        "可先用 scripts/probe_comment_dom.py 在真实登录态下探路。"
    ),
    "panel_opened_but_no_items": (
        "评论面板已打开，但没有渲染出任何评论项（可能仍在加载或结构已变化）。"
    ),
    "selectors_not_matched": (
        "评论面板容器/评论项未能定位，选择器可能已随页面改版漂移。"
    ),
}
LIMIT_REACHED_WARNING = "达到 limit 截断，结果已抽样；可用 next_cursor 继续翻阅。"


class CommentDailyGuard:
    """Per-video, per-day collection guard stored under ``DATA_DIR``.

    The guard is a local rate limit, not a comment store: it only remembers that
    a video was collected on a given local (Asia/Shanghai) date.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(SHANGHAI))

    def already_collected(self, video_id: str) -> bool:
        entry = self._entries().get(video_id)
        return isinstance(entry, dict) and entry.get("date") == self._today()

    def record(self, video_id: str, fetched_at: str) -> None:
        entries = self._entries()
        entries[video_id] = {"date": self._today(), "fetched_at": fetched_at}
        self._write(entries)

    def _today(self) -> str:
        return self._now().astimezone(SHANGHAI).date().isoformat()

    def _entries(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        entries = payload.get("entries")
        return dict(entries) if isinstance(entries, dict) else {}

    def _write(self, entries: dict[str, Any]) -> None:
        cutoff = (self._now().astimezone(SHANGHAI) - timedelta(days=2)).date().isoformat()
        pruned = {
            str(video_id): entry
            for video_id, entry in entries.items()
            if isinstance(entry, dict) and str(entry.get("date") or "") >= cutoff
        }
        payload = {"version": COMMENT_GUARD_VERSION, "entries": pruned}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


class CommentsService:
    """Collect visible comments for one video (or a few recent videos)."""

    def __init__(
        self,
        settings: Any,
        db: Any | None = None,
        *,
        browser_executor: Any | None = None,
        now: Callable[[], datetime] | None = None,
        guard: CommentDailyGuard | None = None,
        retry_delay: tuple[float, float] | None = None,
        sleep: Callable[[float], None] | None = None,
        risk_cooldown: RiskCooldown | None = None,
        skipped_log: SkippedEventLog | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self._browser_executor = browser_executor
        self._now = now or (lambda: datetime.now(SHANGHAI))
        self._guard = guard or CommentDailyGuard(
            Path(settings.data_dir) / COMMENT_GUARD_FILENAME, now=self._now
        )
        if retry_delay is None:
            retry_min = float(
                getattr(
                    settings,
                    "douyin_comment_nav_retry_min_seconds",
                    NAVIGATION_RETRY_MIN_SECONDS,
                )
            )
            retry_max = float(
                getattr(
                    settings,
                    "douyin_comment_nav_retry_max_seconds",
                    NAVIGATION_RETRY_MAX_SECONDS,
                )
            )
            retry_delay = (retry_min, max(retry_min, retry_max))
        self._retry_delay = retry_delay
        self._sleep = sleep or time.sleep
        self._risk_cooldown = risk_cooldown or RiskCooldown(
            Path(settings.data_dir), now=self._now
        )
        self._skipped_log = skipped_log or SkippedEventLog(
            Path(settings.data_dir), now=self._now
        )
        self._cooldown_hours = int(
            getattr(settings, "douyin_risk_cooldown_hours", 12)
        )

    def get_video_comments(
        self,
        video_id: str,
        limit: int = 50,
        cursor: str | None = None,
        keyword: str | None = None,
        recent: int | None = None,
    ) -> dict[str, Any]:
        limit = self._validate_limit(limit)
        offset = self._parse_cursor(cursor)
        normalized_keyword = str(keyword).strip() if keyword else None
        if normalized_keyword is not None and not normalized_keyword:
            normalized_keyword = None
        target = str(video_id or "").strip()
        if not target:
            raise AppError(VALIDATION_ERROR, "video_id 不能为空。")
        cooldown = self._risk_cooldown.status()
        if cooldown.get("active"):
            self._skipped_log.record("risk_control_cooldown")
            return self._cooldown_skipped_result(target, cooldown)
        if target == "recent":
            recent_count = self._validate_recent(recent)
            result = self._get_recent_comments(
                recent_count,
                limit=limit,
                offset=offset,
                keyword=normalized_keyword,
            )
        else:
            if not VIDEO_ID_PATTERN.match(target):
                raise AppError(
                    VALIDATION_ERROR,
                    "video_id 必须是平台作品 id（字母数字、下划线或连字符）。",
                )
            result = self._get_single_video_comments(
                target,
                limit=limit,
                offset=offset,
                keyword=normalized_keyword,
            )
        if result["status"] == STATUS_ABORTED_RISK_CONTROL:
            self._risk_cooldown.activate(self._cooldown_hours)
        if result["status"] == "skipped" and result.get("reason"):
            self._skipped_log.record(str(result["reason"]))
        return result

    # Single video -----------------------------------------------------

    def _get_single_video_comments(
        self,
        video_id: str,
        *,
        limit: int,
        offset: int,
        keyword: str | None,
    ) -> dict[str, Any]:
        fetched_at = self._now().astimezone(SHANGHAI)
        rules, rule_version, rule_warnings = load_keyword_rules(
            self.settings.data_dir
        )
        if self._guard.already_collected(video_id):
            return self._rate_limited_result(
                video_id, fetched_at, rules, rule_version, rule_warnings
            )
        observation = self._collect(
            video_id,
            limit=limit,
            offset=offset,
            keyword=keyword,
            collected_at=fetched_at,
        )
        result = self._build_result(
            video_id,
            observation,
            limit=limit,
            offset=offset,
            keyword=keyword,
            fetched_at=fetched_at,
            rules=rules,
            rule_version=rule_version,
            rule_warnings=rule_warnings,
        )
        if result["status"] in {STATUS_COMPLETED, STATUS_PARTIAL}:
            self._record_guard(video_id, fetched_at, result["warnings"])
        return result

    def _collect(
        self,
        video_id: str,
        *,
        limit: int,
        offset: int,
        keyword: str | None,
        collected_at: datetime,
    ) -> dict[str, Any]:
        scan_limit = self._scan_limit(limit, keyword)
        if self._browser_executor is None:
            raise AppError(
                DATA_NOT_AVAILABLE,
                "浏览器执行器不可用，无法读取评论面板。",
                retryable=True,
            )
        command = CollectComments(
            video_id=video_id,
            limit=scan_limit,
            cursor_offset=offset,
            collected_at=collected_at.isoformat(),
        )
        attempts = NAVIGATION_RETRY_ATTEMPTS + 1
        for attempt in range(attempts):
            try:
                return self._browser_executor.execute(command)
            except Exception as exc:
                if not is_navigation_race(exc):
                    raise
                if attempt >= attempts - 1:
                    raise AppError(
                        NAVIGATION_RACE,
                        "页面在采集过程中发生跳转/上下文销毁，已重试一次仍失败；"
                        "该错误可稍后重试。",
                        retryable=True,
                    ) from exc
                self._sleep(random.uniform(*self._retry_delay))

    def _build_result(
        self,
        video_id: str,
        observation: dict[str, Any],
        *,
        limit: int,
        offset: int,
        keyword: str | None,
        fetched_at: datetime,
        rules: dict[str, tuple[str, ...]],
        rule_version: str,
        rule_warnings: list[str],
    ) -> dict[str, Any]:
        warnings = list(rule_warnings)
        state = str(observation.get("state") or STATE_UNKNOWN)
        if state != STATE_VIDEO_NOT_PUBLIC:
            final_url_warning = self._final_url_warning(observation, video_id)
            if final_url_warning:
                warnings.append(final_url_warning)
        base = self._base_payload(video_id, fetched_at)

        if state == STATE_RISK_CONTROL:
            comments = list(observation.get("items") or [])
            return {
                **base,
                "status": STATUS_ABORTED_RISK_CONTROL,
                "error_code": "RISK_CONTROL",
                "message": RISK_MESSAGE,
                "comments": comments,
                "total": len(comments),
                "total_comment_count": observation.get("total_comment_count"),
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights(comments, rules, rule_version),
                "warnings": warnings,
            }
        if state == STATE_VIDEO_NOT_PUBLIC:
            return {
                **base,
                "status": "skipped",
                "error_code": "VIDEO_NOT_PUBLIC",
                "reason": "video_not_public",
                "retryable": False,
                "message": REASON_MESSAGES["video_not_public"],
                "comments": [],
                "total": 0,
                "total_comment_count": None,
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights([], rules, rule_version),
                "warnings": [],
            }
        if state == STATE_COMMENTS_DISABLED:
            return {
                **base,
                "status": STATUS_PARSE_FAILED,
                "error_code": "COMMENTS_DISABLED",
                "reason": "comments_disabled",
                "message": REASON_MESSAGES["comments_disabled"],
                "comments": [],
                "total": 0,
                "total_comment_count": observation.get("total_comment_count"),
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights([], rules, rule_version),
                "warnings": warnings,
            }
        if state == STATE_LOGIN_REQUIRED:
            return {
                **base,
                "status": STATUS_USER_ACTION_REQUIRED,
                "error_code": "LOGIN_REQUIRED",
                "message": LOGIN_MESSAGE,
                "comments": [],
                "total": 0,
                "total_comment_count": None,
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights([], rules, rule_version),
                "warnings": warnings,
            }
        if state == STATE_UNKNOWN:
            reason = observation.get("reason")
            if reason:
                warnings.append(f"reason={reason}")
            else:
                warnings.append(
                    f"未匹配到评论项选择器（panel_found={bool(observation.get('panel_found'))}）。"
                )
            return {
                **base,
                "status": STATUS_PARSE_FAILED,
                "error_code": "DOM_MISMATCH",
                "reason": reason,
                "message": REASON_MESSAGES.get(reason, PARSE_FAILED_MESSAGE),
                "comments": [],
                "total": 0,
                "total_comment_count": observation.get("total_comment_count"),
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights([], rules, rule_version),
                "warnings": warnings,
            }

        comments = list(observation.get("items") or [])
        if keyword:
            needle = keyword.casefold()
            comments = [
                comment
                for comment in comments
                if needle in str(comment.get("content") or "").casefold()
            ]
        truncated_by_window = len(comments) > limit
        comments = comments[:limit]
        exhausted = bool(observation.get("exhausted"))
        total_known = observation.get("total_comment_count")
        if total_known is not None:
            total_known = int(total_known)
        hit_limit = int(observation.get("raw_item_count") or 0) >= self._scan_limit(
            limit, keyword
        ) and not exhausted
        if hit_limit or truncated_by_window:
            warnings.append(LIMIT_REACHED_WARNING)

        sampled, completeness = self._sampling(
            returned=len(comments),
            total_known=total_known,
            hit_limit=hit_limit or truncated_by_window,
        )
        if state == STATE_EMPTY and not comments and not (total_known or 0):
            # Positive empty state is a complete answer even if the panel does
            # not render a numeric total.
            sampled, completeness = False, "complete"
        if not comments and (total_known or 0) > 0:
            # The panel declares N>0 comments but none parsed: structure drift.
            warnings.append(
                f"页面声明 {total_known} 条评论但解析到 0 条，按结构不匹配处理。"
            )
            return {
                **base,
                "status": STATUS_PARSE_FAILED,
                "error_code": "DOM_MISMATCH",
                "reason": (
                    "panel_opened_but_no_items"
                    if observation.get("panel_found") is not False
                    else "selectors_not_matched"
                ),
                "message": REASON_MESSAGES["panel_opened_but_no_items"],
                "comments": [],
                "total": 0,
                "total_comment_count": total_known,
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights([], rules, rule_version),
                "warnings": warnings,
            }

        has_more = not exhausted
        next_cursor = None
        if has_more:
            next_offset = offset + int(observation.get("raw_item_count") or 0)
            if next_offset > offset:
                next_cursor = f"offset:{next_offset}"
            else:
                has_more = False

        dropped = int(observation.get("dropped_item_count") or 0)
        status = STATUS_COMPLETED
        if dropped and comments:
            status = STATUS_PARTIAL
            warnings.append(f"{dropped} 条评论项缺少可解析内容，已跳过。")
        elif dropped and not comments:
            # Items were rendered but none carried id/content: structure drift,
            # not an empty comment section.
            warnings.append(f"{dropped} 条评论项均无法解析，按结构不匹配处理。")
            return {
                **base,
                "status": STATUS_PARSE_FAILED,
                "error_code": "DOM_MISMATCH",
                "reason": "selectors_not_matched",
                "message": PARSE_FAILED_MESSAGE,
                "comments": [],
                "total": 0,
                "total_comment_count": total_known,
                "sampled": None,
                "completeness": "unknown",
                "has_more": False,
                "next_cursor": None,
                "insights": build_insights([], rules, rule_version),
                "warnings": warnings,
            }
        if state == STATE_EMPTY:
            has_more = False
            next_cursor = None

        result = {
            **base,
            "status": status,
            "error_code": None,
            "message": None,
            "comments": comments,
            "total": len(comments),
            "total_comment_count": total_known,
            "sampled": sampled,
            "completeness": completeness,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "insights": build_insights(comments, rules, rule_version),
            "warnings": warnings,
        }
        return result

    # Recent videos ----------------------------------------------------

    def _get_recent_comments(
        self,
        recent: int,
        *,
        limit: int,
        offset: int,
        keyword: str | None,
    ) -> dict[str, Any]:
        videos = self._recent_video_ids(recent)
        if not videos:
            raise AppError(
                DATA_NOT_AVAILABLE,
                "本地没有可用的近期作品，请先同步作品列表。",
                retryable=True,
            )
        fetched_at = self._now().astimezone(SHANGHAI)
        rules, rule_version, rule_warnings = load_keyword_rules(
            self.settings.data_dir
        )
        warnings = list(rule_warnings)
        merged: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        remaining = limit
        limited_ids: list[str] = []
        hit_limit = False
        aborted = False
        login_required = False
        parse_failed = False
        failed_any = False
        skipped_any = False
        for video_id in videos:
            if remaining <= 0:
                break
            if self._guard.already_collected(video_id):
                limited_ids.append(video_id)
                summaries.append(
                    {
                        "video_id": video_id,
                        "status": STATUS_COMPLETED,
                        "total": 0,
                        "rate_limited": True,
                    }
                )
                continue
            observation = self._collect(
                video_id,
                limit=remaining,
                offset=offset,
                keyword=keyword,
                collected_at=fetched_at,
            )
            single = self._build_result(
                video_id,
                observation,
                limit=remaining,
                offset=offset,
                keyword=keyword,
                fetched_at=fetched_at,
                rules=rules,
                rule_version=rule_version,
                rule_warnings=rule_warnings,
            )
            warnings.extend(single.get("warnings") or [])
            summaries.append(
                {
                    "video_id": video_id,
                    "status": single["status"],
                    "total": single["total"],
                    "rate_limited": single.get("rate_limited", False),
                }
            )
            if single["status"] == STATUS_ABORTED_RISK_CONTROL:
                aborted = True
            elif single["status"] == STATUS_USER_ACTION_REQUIRED:
                login_required = True
            elif single["status"] == STATUS_PARSE_FAILED:
                parse_failed = True
            elif single["status"] == STATUS_PARTIAL:
                failed_any = True
            elif single["status"] == "skipped":
                skipped_any = True
            for comment in single.get("comments") or []:
                merged.append({**comment, "video_id": video_id})
            remaining -= int(single["total"])
            if single["status"] in {STATUS_COMPLETED, STATUS_PARTIAL}:
                self._record_guard(video_id, fetched_at, warnings)
            if single.get("completeness") == "truncated":
                hit_limit = True
            if aborted:
                break

        if aborted:
            status = STATUS_ABORTED_RISK_CONTROL
            error_code = "RISK_CONTROL"
            message = RISK_MESSAGE
        elif login_required and not merged:
            status = STATUS_USER_ACTION_REQUIRED
            error_code = "LOGIN_REQUIRED"
            message = LOGIN_MESSAGE
        elif skipped_any and not merged and len(limited_ids) + 1 >= len(videos):
            status = "skipped"
            error_code = "VIDEO_NOT_PUBLIC"
            message = REASON_MESSAGES["video_not_public"]
        elif parse_failed and not merged:
            status = STATUS_PARSE_FAILED
            error_code = "DOM_MISMATCH"
            message = PARSE_FAILED_MESSAGE
        elif failed_any or len(limited_ids) == len(videos):
            status = STATUS_COMPLETED if not merged else STATUS_PARTIAL
            error_code = None
            message = None
        else:
            status = STATUS_COMPLETED
            error_code = None
            message = None

        # Cross-video totals are not a platform-displayed number, so recent mode
        # never fabricates one.
        sampled, completeness = self._sampling(
            returned=len(merged),
            total_known=None,
            hit_limit=hit_limit,
        )
        has_more = False
        next_cursor = None
        if remaining <= 0 and len(merged) >= limit and status == STATUS_COMPLETED:
            next_cursor = f"offset:{offset + limit}"
            has_more = True
            sampled, completeness = self._sampling(
                returned=len(merged), total_known=None, hit_limit=True
            )
            warnings.append(LIMIT_REACHED_WARNING)
        if limited_ids:
            warnings.append(RATE_LIMITED_MESSAGE)
        return {
            **self._base_payload("recent", fetched_at),
            "status": status,
            "error_code": error_code,
            "message": message,
            "comments": merged,
            "total": len(merged),
            "total_comment_count": None,
            "sampled": sampled,
            "completeness": completeness,
            "rate_limited": bool(limited_ids),
            "rate_limited_video_ids": limited_ids,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "insights": build_insights(merged, rules, rule_version),
            "videos": summaries,
            "warnings": warnings,
        }

    def _recent_video_ids(self, recent: int) -> list[str]:
        if self.db is None:
            raise AppError(
                DATA_NOT_AVAILABLE, "本地数据库不可用，无法解析 recent 作品。"
            )
        rows = self.db.query_all(
            "SELECT id, video_id, item_id FROM videos "
            "WHERE account_id = ? AND is_active = 1 "
            "ORDER BY publish_time DESC LIMIT ?",
            (BROWSER_DEFAULT_ACCOUNT_ID, recent),
            read_only=True,
        )
        ids: list[str] = []
        for row in rows:
            platform_id = str(
                row.get("video_id") or row.get("item_id") or ""
            ).strip()
            if platform_id and VIDEO_ID_PATTERN.match(platform_id):
                ids.append(platform_id)
        return ids

    # Shared helpers ---------------------------------------------------

    def _base_payload(self, video_id: str, fetched_at: datetime) -> dict[str, Any]:
        return {
            "video_id": video_id,
            "fetched_at": fetched_at.astimezone(SHANGHAI)
            .replace(microsecond=0)
            .isoformat(),
            "rate_limited": False,
            "reason": None,
            "parser_version": COMMENT_PARSER_VERSION,
        }

    def _final_url_warning(
        self, observation: dict[str, Any], video_id: str
    ) -> str | None:
        final_url = str(observation.get("final_url") or "").strip()
        if not final_url or is_not_public_url(final_url):
            return None
        requested = str(
            self.settings.douyin_web_video_url_template.format(video_id=video_id)
        ).strip()
        if final_url.rstrip("/") == requested.rstrip("/"):
            return None
        return f"final_url={sanitize_final_url(final_url)}"

    def _cooldown_skipped_result(
        self, video_id: str, cooldown: dict[str, Any]
    ) -> dict[str, Any]:
        fetched_at = self._now().astimezone(SHANGHAI)
        rules, rule_version, _ = load_keyword_rules(self.settings.data_dir)
        remaining = int(cooldown.get("remaining_seconds") or 0)
        hours = max(1, math.ceil(remaining / 3600)) if remaining else 0
        return {
            **self._base_payload(video_id, fetched_at),
            "status": "skipped",
            "error_code": "RISK_CONTROL_COOLDOWN",
            "reason": "risk_control_cooldown",
            "retryable": False,
            "cooldown_until": cooldown.get("cooldown_until"),
            "remaining_seconds": remaining,
            "message": (
                f"账号风控冷却中，剩余约 {hours} 小时（{remaining} 秒）；"
                + REASON_MESSAGES["risk_control_cooldown"]
            ),
            "comments": [],
            "total": 0,
            "total_comment_count": None,
            "sampled": None,
            "completeness": "unknown",
            "has_more": False,
            "next_cursor": None,
            "insights": build_insights([], rules, rule_version),
            "warnings": [],
        }

    def _rate_limited_result(
        self,
        video_id: str,
        fetched_at: datetime,
        rules: dict[str, tuple[str, ...]],
        rule_version: str,
        rule_warnings: list[str],
    ) -> dict[str, Any]:
        warnings = [*rule_warnings, RATE_LIMITED_MESSAGE]
        return {
            **self._base_payload(video_id, fetched_at),
            "status": STATUS_COMPLETED,
            "error_code": None,
            "message": RATE_LIMITED_MESSAGE,
            "comments": [],
            "total": 0,
            "total_comment_count": None,
            "sampled": None,
            "completeness": "unknown",
            "rate_limited": True,
            "has_more": False,
            "next_cursor": None,
            "insights": build_insights([], rules, rule_version),
            "warnings": warnings,
        }

    def _record_guard(
        self, video_id: str, fetched_at: datetime, warnings: list[str]
    ) -> None:
        try:
            self._guard.record(
                video_id,
                fetched_at.astimezone(SHANGHAI)
                .replace(microsecond=0)
                .isoformat(),
            )
        except OSError:
            warnings.append("本地频率限制状态写入失败，本次成功采集但未能记账。")

    @staticmethod
    def _sampling(
        *, returned: int, total_known: int | None, hit_limit: bool
    ) -> tuple[bool | None, str]:
        if total_known is not None:
            if returned < total_known:
                return True, "truncated"
            return False, "complete"
        if hit_limit:
            return True, "truncated"
        return None, "unknown"

    @staticmethod
    def _scan_limit(limit: int, keyword: str | None) -> int:
        if not keyword:
            return limit
        return min(MAX_COMMENT_LIMIT, max(limit * 4, limit + 50))

    @staticmethod
    def _validate_limit(limit: Any) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise AppError(VALIDATION_ERROR, "limit 必须是 1..200 的整数。")
        if not 1 <= limit <= MAX_COMMENT_LIMIT:
            raise AppError(VALIDATION_ERROR, "limit 必须在 1 到 200 之间。")
        return limit

    @staticmethod
    def _validate_recent(recent: Any) -> int:
        if recent is None:
            return RECENT_DEFAULT
        if not isinstance(recent, int) or isinstance(recent, bool):
            raise AppError(VALIDATION_ERROR, "recent 必须是 1..20 的整数。")
        if not 1 <= recent <= RECENT_MAX:
            raise AppError(VALIDATION_ERROR, "recent 必须在 1 到 20 之间。")
        return recent

    @staticmethod
    def _parse_cursor(cursor: Any) -> int:
        if cursor is None:
            return 0
        text = str(cursor).strip()
        match = CURSOR_PATTERN.match(text)
        if not match:
            raise AppError(
                VALIDATION_ERROR,
                "cursor 必须来自上一次返回的 next_cursor（形如 offset:50）。",
            )
        return int(match.group(1))


def is_navigation_race(exc: BaseException) -> bool:
    """Classify Playwright navigation/context teardown errors as retryable."""

    if isinstance(exc, AppError):
        return exc.error_type == NAVIGATION_RACE
    message = " ".join(str(part) for part in (exc.args or ())) + " " + str(exc)
    lowered = message.lower()
    markers = (
        "execution context was destroyed",
        "most likely because of a navigation",
        "context was destroyed",
        "target page, context or browser has been closed",
        "navigation",
        "err_aborted",
    )
    return any(marker in lowered for marker in markers)
