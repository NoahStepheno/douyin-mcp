"""Visible-DOM comment panel reader for Douyin video pages.

This module only reads what the page has already rendered. It never calls a
private endpoint, never bypasses a verification challenge and never retries a
risk-control prompt. Every retry-sensitive decision is surfaced to the caller
via the ``state`` field instead.

Observation contract (returned by :func:`collect_comments`)::

    {
      "state": "comments" | "empty" | "risk_control" | "login_required" | "unknown",
      "container_found": bool,       # comment list container matched a selector
      "items": [normalized comment], # window [cursor_offset : cursor_offset + limit]
      "raw_item_count": int,         # number of items in that window
      "total_comment_count": int | None,  # platform-declared total, never estimated
      "exhausted": bool,             # no more items can be loaded in this page
      "read_count": int,             # DOM reads performed (diagnostics only)
      "parser_version": "comment-panel-v1",
    }

Comment field missing semantics (v1):

* ``comment_id``: platform attribute when present, otherwise a stable local
  digest of video id + nickname + content + raw time text. Never a user id.
* ``author_nickname``: ``""`` when the panel does not render a nickname.
* ``like_count`` / ``reply_count``: ``None`` when not displayed; never faked 0.
* ``posted_at``: ``None`` when the text cannot be parsed; ``posted_at_precision``
  is then ``"unknown"``. Relative texts are back-dated from ``fetched_at`` and
  marked ``"approx"``. Full date+time texts are marked ``"exact"``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

COMMENT_PARSER_VERSION = "comment-panel-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")

#: Upper bound shared by the MCP tool and the collector (ticket section 2).
MAX_COMMENT_LIMIT = 200
#: Internal page size: scroll in blocks of this many comments.
COMMENT_PAGE_SIZE = 50
#: Hard cap on scroll rounds per collection to keep one call bounded.
MAX_SCROLL_ROUNDS = 40

STATE_COMMENTS = "comments"
STATE_EMPTY = "empty"
STATE_RISK_CONTROL = "risk_control"
STATE_LOGIN_REQUIRED = "login_required"
STATE_UNKNOWN = "unknown"


_COMMENT_PANEL_SCRIPT = r"""
() => {
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const body = document.body;
  const pageText = norm(body ? body.innerText : '');
  const visible = el => {
    if (!el) return false;
    if (el.getAttribute && el.getAttribute('aria-hidden') === 'true') return false;
    if (window.getComputedStyle) {
      const style = window.getComputedStyle(el);
      if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    }
    return true;
  };
  const textOf = node => norm(node ? (node.innerText || '') : '');

  const riskText = /(滑块验证|拖动滑块|请完成验证|安全验证|人机验证|操作频繁|访问频繁|异常流量|检测到风险)/;
  const loginText = /(扫码登录|登录后查看|请先登录|手机号登录|验证码登录|登录后参与评论)/;
  const emptyText = /(暂无评论|还没有评论|来抢首评|暂时没有评论|还没有人评论)/;
  const endText = /(没有更多评论|已显示全部评论|到底了|暂无更多评论)/;
  const moreText = /(展开更多|查看更多评论|加载更多评论|查看全部评论)/;

  const riskSelectors = [
    '#captcha_container', '[id*="captcha"]', '[class*="captcha"]',
    '[data-e2e*="captcha"]', '[class*="verify-wrap"]', '[class*="verify_modal"]',
    '[class*="verifyModal"]'
  ];
  const riskElement = riskSelectors.some(sel => {
    try {
      return [...document.querySelectorAll(sel)].some(visible);
    } catch (error) {
      return false;
    }
  });

  const containerSelectors = [
    '[data-e2e="comment-list"]', '[class*="comment-list"]',
    '[class*="commentList"]', '[class*="comment-panel"]',
    '[class*="commentPanel"]', '[class*="comment-container"]'
  ];
  let container = null;
  for (const sel of containerSelectors) {
    const node = document.querySelector(sel);
    if (node && visible(node)) { container = node; break; }
  }

  const itemSelectors = [
    '[data-e2e="comment-item"]', '[class*="comment-item"]',
    '[class*="commentItem"]'
  ];
  let itemNodes = [];
  let matchedItemSelector = '';
  for (const sel of itemSelectors) {
    const nodes = [...document.querySelectorAll(sel)].filter(visible);
    if (nodes.length) { itemNodes = nodes; matchedItemSelector = sel; break; }
  }
  itemNodes = itemNodes.filter(node =>
    !itemNodes.some(other => other !== node && other.contains(node))
  );

  const leafTexts = root => [...root.querySelectorAll('*')]
    .filter(el => el.children.length === 0)
    .map(el => norm(el.innerText))
    .filter(Boolean);

  const firstText = (root, selectors) => {
    for (const sel of selectors) {
      const node = root.querySelector(sel);
      if (node) {
        const text = textOf(node);
        if (text) return text;
      }
    }
    return '';
  };

  const timePattern = /^(刚刚|\d+\s*(秒|分钟|小时|天|周|个月|年)前|(今天|昨天|前天)\s*\d{1,2}:\d{2}|\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2})?|\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(\s+\d{1,2}:\d{2})?|\d{1,2}:\d{2})$/;
  const countPattern = /(\d+(?:\.\d+)?)\s*([万亿]?)/;

  const items = itemNodes.map(node => {
    const itemText = textOf(node);
    const leaves = leafTexts(node);
    const commentId = node.getAttribute('data-comment-id') ||
      node.getAttribute('data-id') ||
      (node.id && /^\d{6,}$/.test(node.id) ? node.id : '') || '';
    let content = firstText(node, [
      '[data-e2e="comment-content"]', '[class*="comment-content"]',
      '[class*="commentContent"]', '[class*="comment-main"]'
    ]);
    if (!content) {
      const nickname = firstText(node, [
        '[data-e2e="comment-author"]', '[class*="user-name"]',
        '[class*="nickname"]', 'a[href*="/user/"]'
      ]);
      const candidates = leaves.filter(text =>
        text !== nickname && !timePattern.test(text) && !/^\d+(\.\d+)?\s*[万亿]?$/.test(text)
      );
      candidates.sort((a, b) => b.length - a.length);
      content = candidates.length ? candidates[0] : '';
    }
    const nickname = firstText(node, [
      '[data-e2e="comment-author"]', '[class*="user-name"]',
      '[class*="nickname"]', 'a[href*="/user/"]'
    ]);
    const timeText = leaves.find(text => timePattern.test(text)) || '';
    const likeText = firstText(node, [
      '[data-e2e="comment-like-count"]', '[class*="like-count"]',
      '[class*="likeCount"]', '[class*="digg"]'
    ]);
    const replyMatch = itemText.match(/(\d+)\s*条?回复/);
    const authorTag = [...node.querySelectorAll(
      '[data-e2e*="author-tag"], [class*="author-tag"], [class*="authorTag"], [class*="badge"]'
    )].some(el => textOf(el) === '作者');
    const pinned = /(^|\s)置顶/.test(itemText) ||
      Boolean(node.querySelector('[class*="pin"], [data-e2e*="top"]'));
    return {
      comment_id: commentId,
      content,
      nickname,
      like_text: likeText,
      reply_text: replyMatch ? replyMatch[1] : '',
      time_text: timeText,
      is_pinned: pinned,
      is_author_reply: authorTag
    };
  });

  const containerText = norm(container ? container.innerText : '');
  const headerText = firstText(document, [
    '[data-e2e="comment-count"]', '[class*="comment-count"]',
    '[class*="commentCount"]', '[class*="comment-title"]',
    '[class*="commentTitle"]'
  ]);
  let totalCommentCount = null;
  const countSource = headerText || containerText.slice(0, 160) || pageText.slice(0, 160);
  const explicitTotal = countSource.match(/(?:共\s*)?([\d][\d,.]*)\s*条评论/);
  if (explicitTotal) {
    const numeric = Number(explicitTotal[1].replace(/[,.]/g, ''));
    if (Number.isFinite(numeric)) totalCommentCount = numeric;
  }

  let state = 'unknown';
  if (riskElement || riskText.test(pageText)) state = 'risk_control';
  else if (loginText.test(pageText) && items.length === 0) state = 'login_required';
  else if (items.length || container) {
    state = items.length ? 'comments' : (emptyText.test(pageText) ? 'empty' : 'unknown');
  } else if (emptyText.test(pageText)) {
    state = 'empty';
  }

  let hasMoreHint = null;
  if (endText.test(pageText)) hasMoreHint = false;
  else if (moreText.test(pageText)) hasMoreHint = true;

  return {
    state,
    container_found: Boolean(container),
    matched_item_selector: matchedItemSelector,
    items,
    total_comment_count: totalCommentCount,
    has_more_hint: hasMoreHint,
    page_text: pageText.slice(0, 400)
  };
}
"""

_SCROLL_COMMENT_PANEL_SCRIPT = r"""
() => {
  const candidates = [
    '[data-e2e="comment-list"]', '[class*="comment-list"]',
    '[class*="commentList"]', '[class*="comment-panel"]',
    '[class*="commentPanel"]'
  ];
  for (const sel of candidates) {
    const node = document.querySelector(sel);
    if (node && node.scrollHeight > node.clientHeight + 4) {
      node.scrollTop = node.scrollHeight;
      return 'panel';
    }
  }
  window.scrollTo(0, document.documentElement.scrollHeight);
  return 'window';
}
"""

def read_comment_panel(page: Any) -> dict[str, Any]:
    """Read one snapshot of the visible comment panel without any retry."""

    raw = page.evaluate(_COMMENT_PANEL_SCRIPT)
    return raw if isinstance(raw, dict) else {}


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


def parse_comment_time(
    value: Any, collected_at: datetime
) -> tuple[str | None, str]:
    """Back-date a rendered comment time and report its precision.

    Returns ``(iso8601_or_none, precision)`` with precision in
    ``{"exact", "approx", "unknown"}``. Relative texts are subtracted from
    ``collected_at`` (Asia/Shanghai) and always reported as ``approx``.
    """

    text = re.sub(r"\s+", " ", str(value or "")).strip()
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

    date_value, has_time = _split_date_time(text, reference)
    if date_value is not None:
        return _iso(date_value), ("exact" if has_time else "approx")

    return None, "unknown"


def _split_date_time(
    text: str, reference: datetime
) -> tuple[datetime | None, bool]:
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
                else:
                    year = reference.year
                    month, day, hour, minute = groups
                return datetime(
                    year, month, day, hour, minute, tzinfo=SHANGHAI
                ), True
            if len(groups) == 3:
                year, month, day = groups
            else:
                year = reference.year
                month, day = groups
            moment = datetime(year, month, day, tzinfo=SHANGHAI)
            if moment - reference > timedelta(days=1):
                moment = moment.replace(year=year - 1)
            return moment, False
        except ValueError:
            return None, False
    return None, False


def _iso(moment: datetime) -> str:
    return moment.astimezone(SHANGHAI).replace(microsecond=0).isoformat()


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
    max_scroll_rounds: int = MAX_SCROLL_ROUNDS,
    page_size: int = COMMENT_PAGE_SIZE,
) -> dict[str, Any]:
    """Read the visible comment panel, stopping on any risk-control prompt.

    Risk control is checked on the very first read and after every scroll. Once
    detected the function returns immediately; no retry, no second navigation.
    """

    if limit < 1:
        raise ValueError("limit must be a positive integer")
    if cursor_offset < 0:
        raise ValueError("cursor_offset must be non-negative")
    collected_at = collected_at or datetime.now(SHANGHAI)
    window_end = cursor_offset + limit
    buffer_size = max(window_end, page_size)

    ordered: dict[str, dict[str, Any]] = {}
    ordered_items: list[dict[str, Any]] = []
    total_comment_count: int | None = None
    state = STATE_UNKNOWN
    container_found = False
    exhausted = False
    has_more_hint: bool | None = None
    read_count = 0
    stable_rounds = 0
    previous_count = -1
    scroll_rounds = 0
    dropped = 0

    while True:
        raw = read_comment_panel(page)
        read_count += 1
        state = str(raw.get("state") or STATE_UNKNOWN)
        if state == STATE_RISK_CONTROL:
            break
        if state == STATE_LOGIN_REQUIRED:
            break
        container_found = container_found or bool(raw.get("container_found"))
        if raw.get("total_comment_count") is not None:
            parsed_total = _parse_count_text(raw.get("total_comment_count"))
            if parsed_total is not None:
                total_comment_count = parsed_total
        hint = raw.get("has_more_hint")
        if hint is not None:
            has_more_hint = bool(hint)

        raw_items = raw.get("items")
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

        if state == STATE_EMPTY:
            exhausted = True
            break
        current_count = len(ordered_items)
        if current_count >= window_end:
            # Requested window is satisfied; whether more pages exist is not yet
            # known, so the caller must treat this as "possibly has more".
            if has_more_hint is False:
                exhausted = True
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
        page.evaluate(_SCROLL_COMMENT_PANEL_SCRIPT)
        scroll_rounds += 1
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_for_timeout) and settle_ms > 0:
            wait_for_timeout(settle_ms)

    state = state if state in {
        STATE_COMMENTS,
        STATE_EMPTY,
        STATE_RISK_CONTROL,
        STATE_LOGIN_REQUIRED,
    } else STATE_UNKNOWN
    if state == STATE_UNKNOWN and ordered_items:
        state = STATE_COMMENTS

    buffer_items = ordered_items[:buffer_size]
    window = buffer_items[cursor_offset:window_end]
    return {
        "state": state,
        "container_found": container_found,
        "items": window,
        "raw_item_count": len(window),
        "seen_item_count": len(ordered_items),
        "dropped_item_count": dropped,
        "total_comment_count": total_comment_count,
        "exhausted": exhausted,
        "read_count": read_count,
        "parser_version": COMMENT_PARSER_VERSION,
    }
