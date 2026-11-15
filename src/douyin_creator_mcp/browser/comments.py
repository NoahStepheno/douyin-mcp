"""Visible comment panel reading for Douyin video pages (parser v2).

The public video URL redirects to the ``/jingxuan?modal_id=...`` layer where the
comment drawer is **closed**, so the panel must be opened before any comment
node exists. Opening, wait signals and structural item extraction live in
``browser/comment_panel.py`` and are shared with ``scripts/probe_comment_dom.py``.

Observation contract (returned by :func:`collect_comments`)::

    {
      "state": "comments" | "empty" | "risk_control" | "login_required" | "unknown",
      "reason": None | "panel_not_opened" | "panel_opened_but_no_items" | "selectors_not_matched",
      "panel_found": bool,
      "opened_by": str | None,       # trigger strategy that opened the drawer
      "items": [normalized comment],
      "raw_item_count": int,
      "seen_item_count": int,
      "dropped_item_count": int,
      "total_comment_count": int | None,
      "exhausted": bool,
      "read_count": int,
      "parser_version": "comment-panel-v2",
    }

Comment field missing semantics (unchanged from v1):

* ``comment_id``: platform attribute when present, otherwise a stable local
  digest of video id + nickname + content + raw time text.
* ``author_nickname``: ``""`` when the panel does not render a nickname.
* ``like_count`` / ``reply_count``: ``None`` when not displayed; never faked 0.
* ``posted_at``: ``None`` when unparseable; precision ``"unknown"`` then.
  Relative texts are back-dated from ``fetched_at`` and marked ``"approx"``;
  full date+time texts are ``"exact"``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .comment_panel import (
    open_comment_panel,
    read_comment_items,
    read_page_signals,
    scroll_comment_panel,
)

COMMENT_PARSER_VERSION = "comment-panel-v2"
SHANGHAI = ZoneInfo("Asia/Shanghai")

#: Upper bound shared by the MCP tool and the collector (ticket section 2).
MAX_COMMENT_LIMIT = 200
#: Internal page size: scroll/read in blocks of this many comments.
COMMENT_PAGE_SIZE = 50
#: Hard cap on scroll rounds per collection to keep one call bounded.
MAX_SCROLL_ROUNDS = 40

STATE_COMMENTS = "comments"
STATE_EMPTY = "empty"
STATE_RISK_CONTROL = "risk_control"
STATE_LOGIN_REQUIRED = "login_required"
STATE_COMMENTS_DISABLED = "comments_disabled"
STATE_UNKNOWN = "unknown"

REASON_COMMENTS_DISABLED = "comments_disabled"
REASON_PANEL_NOT_OPENED = "panel_not_opened"
REASON_PANEL_OPENED_BUT_NO_ITEMS = "panel_opened_but_no_items"
REASON_SELECTORS_NOT_MATCHED = "selectors_not_matched"


def _parse_count_text(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万亿]?)", text)
    if not match:
        return None
    multiplier = {"": 1, "万": 10_000, "亿": 100_000_000}[match.group(2)]
    try:
        return int(float(match.group(1)) * multiplier)
    except ValueError:
        return None


_TOTAL_PATTERNS = (
    re.compile(r"共\s*(\d[\d,.]*)\s*([万亿]?)\s*条评论"),
    re.compile(r"全部评论\s*[（(]\s*(\d[\d,.]*)\s*([万亿]?)\s*[)）]"),
    re.compile(r"评论\s*[（(]\s*(\d[\d,.]*)\s*([万亿]?)\s*[)）]"),
    re.compile(r"(\d[\d,.]*)\s*([万亿]?)\s*条评论"),
)


def parse_total_comment_count(text: Any) -> int | None:
    """Parse the platform-declared total from header text only.

    Never derives the total from the number of parsed items. Returns ``None``
    when the panel does not display a numeric total.
    """

    source = str(text or "")
    if not source:
        return None
    for pattern in _TOTAL_PATTERNS:
        match = pattern.search(source)
        if not match:
            continue
        multiplier = {"": 1, "万": 10_000, "亿": 100_000_000}.get(
            match.group(2) if match.lastindex and match.lastindex >= 2 else ""
        )
        try:
            number = float(match.group(1).replace(",", ""))
            return int(number * multiplier)
        except (ValueError, TypeError):
            continue
    return None


def _dedupe_key(item: dict[str, Any], video_id: str) -> str:
    explicit = str(item.get("comment_id") or "").strip()
    if explicit:
        return explicit
    digest = hashlib.sha256(
        "|".join(
            [
                video_id,
                str(item.get("nickname") or ""),
                str(item.get("content") or ""),
                str(item.get("time_text") or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _iso(moment: datetime) -> str:
    return moment.astimezone(SHANGHAI).replace(microsecond=0).isoformat()


_TIME_TAIL_RE = re.compile(r"\s*[·•]\s*[^\s·•]+\s*$")
_TIME_NOISE_RE = re.compile(r"\s*(作者赞过|置顶)\s*$")


def _clean_time_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for _ in range(2):
        # Action/noise fragments can trail the timestamp before/after the region.
        text = _TIME_NOISE_RE.sub("", text).strip()
        # Real DOM appends the IP region: "1小时前·云南", "1天前·河北".
        text = _TIME_TAIL_RE.sub("", text).strip()
    return text


def parse_comment_time(
    value: Any, collected_at: datetime
) -> tuple[str | None, str]:
    """Back-date a rendered comment time and report its precision.

    Precision ladder (v2):

    * ``刚刚`` / ``N 分钟前`` / ``N 小时前`` / ``N 天前`` / ``昨天 HH:MM`` /
      ``前天`` -> ``approx`` (relative to ``collected_at``);
    * full date containing a year (``2026-11-14`` / ``2026年11月14日``) ->
      ``exact``;
    * month-day only (``11-14``), year inferred -> ``approx``;
    * unparseable -> ``(None, "unknown")``.
    """

    text = _clean_time_text(value)
    if not text:
        return None, "unknown"
    reference = collected_at.astimezone(SHANGHAI)

    if text in {"刚刚", "刚刚发布"}:
        return _iso(reference), "approx"

    relative = re.match(r"^(\d+)\s*(秒|分钟|小时|天|周|个月|年)前$", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {
            "秒": timedelta(seconds=amount),
            "分钟": timedelta(minutes=amount),
            "小时": timedelta(hours=amount),
            "天": timedelta(days=amount),
            "周": timedelta(days=amount * 7),
            "个月": timedelta(days=amount * 30),
            "年": timedelta(days=amount * 365),
        }[unit]
        return _iso(reference - delta), "approx"

    day_relative = re.match(r"^(今天|昨天|前天)\s*(\d{1,2}):(\d{2})$", text)
    if day_relative:
        offset = {"今天": 0, "昨天": 1, "前天": 2}[day_relative.group(1)]
        moment = (reference - timedelta(days=offset)).replace(
            hour=int(day_relative.group(2)),
            minute=int(day_relative.group(3)),
            second=0,
            microsecond=0,
        )
        return _iso(moment), "approx"

    if text in {"昨天", "前天", "今天"}:
        offset = {"今天": 0, "昨天": 1, "前天": 2}[text]
        moment = (reference - timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return _iso(moment), "approx"

    time_only = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if time_only:
        moment = reference.replace(
            hour=int(time_only.group(1)),
            minute=int(time_only.group(2)),
            second=0,
            microsecond=0,
        )
        if moment > reference:
            moment -= timedelta(days=1)
        return _iso(moment), "approx"

    date_value, precision = _split_date_time(text, reference)
    if date_value is not None:
        return _iso(date_value), precision

    return None, "unknown"


def _split_date_time(
    text: str, reference: datetime
) -> tuple[datetime | None, str]:
    normalized = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .strip()
    )
    patterns = (
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$", True),
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", False),
        (r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$", True),
        (r"^(\d{1,2})-(\d{1,2})$", False),
    )
    for pattern, has_time in patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue
        groups = [int(value) for value in match.groups()]
        try:
            if has_time:
                if len(groups) == 5:
                    year, month, day, hour, minute = groups
                    precision = "exact"
                else:
                    year = reference.year
                    month, day, hour, minute = groups
                    precision = "approx"
                moment = datetime(
                    year, month, day, hour, minute, tzinfo=SHANGHAI
                )
            elif len(groups) == 3:
                year, month, day = groups
                moment = datetime(year, month, day, tzinfo=SHANGHAI)
                precision = "exact"
            else:
                year = reference.year
                month, day = groups
                moment = datetime(year, month, day, tzinfo=SHANGHAI)
                if moment - reference > timedelta(days=1):
                    moment = moment.replace(year=year - 1)
                precision = "approx"
            return moment, precision
        except ValueError:
            return None, "unknown"
    return None, "unknown"


def normalize_comment_item(
    raw: dict[str, Any],
    *,
    video_id: str,
    collected_at: datetime,
) -> dict[str, Any] | None:
    """Convert one raw DOM item into the public comment shape.

    Returns ``None`` when the item carries neither an id nor any content, which
    the service reports as a dropped/partial item.
    """

    content = re.sub(r"\s+", " ", str(raw.get("content") or "")).strip()
    comment_id = str(raw.get("comment_id") or "").strip()
    if not content and not comment_id:
        return None
    posted_at, precision = parse_comment_time(raw.get("time_text"), collected_at)
    return {
        "comment_id": comment_id or _dedupe_key(raw, video_id),
        "content": content,
        "author_nickname": re.sub(r"\s+", " ", str(raw.get("nickname") or "")).strip(),
        "like_count": _parse_count_text(raw.get("like_text")),
        "reply_count": _parse_count_text(raw.get("reply_text")),
        "posted_at": posted_at,
        "posted_at_precision": precision,
        "is_pinned": bool(raw.get("is_pinned")),
        "is_author_reply": bool(raw.get("is_author_reply")),
    }


def _item_key(item: dict[str, Any]) -> str:
    return str(item.get("comment_id") or "")


def collect_comments(
    page: Any,
    *,
    video_id: str = "",
    limit: int = COMMENT_PAGE_SIZE,
    cursor_offset: int = 0,
    collected_at: datetime | None = None,
    settle_ms: int = 600,
    panel_timeout_ms: int = 8000,
    max_scroll_rounds: int = MAX_SCROLL_ROUNDS,
    page_size: int = COMMENT_PAGE_SIZE,
    panel_opener: Any | None = None,
) -> dict[str, Any]:
    """Open the comment drawer and read its rendered items.

    A risk-control or login signal is checked before any click and again after
    every scroll. Once detected the function returns immediately; no strategy
    retry and no second navigation happens afterwards.
    """

    if limit < 1:
        raise ValueError("limit must be a positive integer")
    if cursor_offset < 0:
        raise ValueError("cursor_offset must be non-negative")
    collected_at = collected_at or datetime.now(SHANGHAI)
    window_end = cursor_offset + limit
    buffer_size = max(window_end, page_size)

    opener = panel_opener or open_comment_panel
    read_count = 0
    ordered: dict[str, dict[str, Any]] = {}
    ordered_items: list[dict[str, Any]] = []
    total_comment_count: int | None = None
    state = STATE_UNKNOWN
    reason: str | None = None
    panel_found = False
    opened_by: str | None = None
    exhausted = False
    has_more_hint: bool | None = None
    stable_rounds = 0
    previous_count = -1
    scroll_rounds = 0
    dropped = 0

    signals = read_page_signals(page)
    read_count += 1
    if signals.get("risk_control_seen"):
        state = STATE_RISK_CONTROL
    elif signals.get("login_wall"):
        state = STATE_LOGIN_REQUIRED
    elif signals.get("comments_disabled_seen"):
        state = STATE_COMMENTS_DISABLED

    open_result: dict[str, Any] = {}
    if state == STATE_UNKNOWN:
        open_result = opener(
            page, settle_ms=settle_ms, timeout_ms=panel_timeout_ms
        )
        opened_by = open_result.get("strategy")
        if open_result.get("risk_control_seen"):
            state = STATE_RISK_CONTROL
        open_signals = open_result.get("signals") or {}
        if state == STATE_UNKNOWN and open_signals.get("login_wall"):
            state = STATE_LOGIN_REQUIRED
        if state == STATE_UNKNOWN and open_signals.get("comments_disabled_seen"):
            state = STATE_COMMENTS_DISABLED

    if state == STATE_UNKNOWN:
        observed = read_comment_items(page)
        read_count += 1
        total_comment_count = parse_total_comment_count(observed.get("header_text"))
        panel_found = bool(observed.get("panel_found"))
        opened = bool(open_result.get("opened")) or bool(
            observed.get("reply_anchor_count")
        )

        if observed.get("reply_anchor_count"):
            state = STATE_COMMENTS
        elif observed.get("comments_disabled_seen"):
            state = STATE_COMMENTS_DISABLED
        elif observed.get("empty_seen"):
            state = STATE_EMPTY
            exhausted = True
        elif opened and panel_found:
            reason = REASON_PANEL_OPENED_BUT_NO_ITEMS
        elif opened and not panel_found:
            reason = REASON_SELECTORS_NOT_MATCHED
        else:
            reason = REASON_PANEL_NOT_OPENED

        while state == STATE_COMMENTS:
            raw_items = observed.get("items")
            if isinstance(raw_items, list):
                for candidate in raw_items:
                    if not isinstance(candidate, dict):
                        continue
                    normalized = normalize_comment_item(
                        candidate, video_id=video_id, collected_at=collected_at
                    )
                    if normalized is None:
                        dropped += 1
                        continue
                    key = _item_key(normalized)
                    if key in ordered:
                        continue
                    ordered[key] = normalized
                    ordered_items.append(normalized)
            if total_comment_count is None:
                total_comment_count = parse_total_comment_count(
                    observed.get("header_text")
                )
            hint = observed.get("has_more_hint")
            if hint is not None:
                has_more_hint = bool(hint)

            current_count = len(ordered_items)
            if current_count >= window_end:
                # Items already rendered beyond the requested window still count
                # as "more", even when the list itself ends here.
                exhausted = has_more_hint is False and current_count <= window_end
                break
            if has_more_hint is False:
                exhausted = True
                break
            if current_count == previous_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            previous_count = current_count
            if stable_rounds >= 2 or scroll_rounds >= max_scroll_rounds:
                exhausted = True
                break

            scroll_comment_panel(page)
            scroll_rounds += 1
            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if callable(wait_for_timeout) and settle_ms > 0:
                wait_for_timeout(settle_ms)
            scroll_signals = read_page_signals(page)
            read_count += 1
            if scroll_signals.get("risk_control_seen"):
                state = STATE_RISK_CONTROL
                reason = None
                break
            if scroll_signals.get("login_wall"):
                state = STATE_LOGIN_REQUIRED
                reason = None
                break
            observed = read_comment_items(page)
            read_count += 1
            panel_found = panel_found or bool(observed.get("panel_found"))

    buffer_items = ordered_items[:buffer_size]
    window = buffer_items[cursor_offset:window_end]
    return {
        "state": state,
        "reason": reason,
        "panel_found": panel_found,
        "opened_by": opened_by,
        "items": window,
        "raw_item_count": len(window),
        "seen_item_count": len(ordered_items),
        "dropped_item_count": dropped,
        "total_comment_count": total_comment_count,
        "exhausted": exhausted,
        "read_count": read_count,
        "parser_version": COMMENT_PARSER_VERSION,
    }
