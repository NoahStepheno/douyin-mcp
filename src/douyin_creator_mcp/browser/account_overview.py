"""Read the creator account overview panel from ``creator-micro/home``.

The page is a main-frame SPA that lazily renders the overview ~10-12s after
navigation. Waiting therefore uses a DOM signal ("账号总览" panel with at least
one numeric metric), never ``networkidle``. All parsing is anchored on visible
Chinese labels, not hashed class names.

Captured DOM facts (2026-11-15, home-1328):
- the overview lives in the active ``role="tabpanel"``; each metric is a
  label ("播放量" / "主页访问量" / 作品点赞 / 作品分享 / 作品评论 / 净增粉丝)
  followed by a numeric node and an optional "较前7日+N" delta;
- the selector prefix is "时间" with options 昨日 / 近7日 / 近30日;
- the page also contains a "最新作品" block whose 播放量 (e.g. 950) is a single
  video, **not** the account total. Parsing is scoped to the active overview
  panel so 950 can never become ``account_play_count``;
- ``#guide_home_fans`` renders 粉丝指数 + number (not a follower total);
- 获赞 has no stable id; locate the text anchor and its sibling number.
"""

from __future__ import annotations

import re
from typing import Any

ACCOUNT_OVERVIEW_PARSER_VERSION = "account-overview-v1"
PERIODS = ("yesterday", "7d", "30d")
PERIOD_LABELS = {"yesterday": "昨日", "7d": "近7日", "30d": "近30日"}

STATE_RENDERED = "rendered"
STATE_NOT_RENDERED = "not_rendered"
STATE_LOGIN_REQUIRED = "login_required"
STATE_RISK_CONTROL = "risk_control"

_JS_HELPERS = r"""
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    if (window.getComputedStyle) {
      const style = window.getComputedStyle(el);
      if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    }
    return true;
  };
  const textOf = el => norm(el ? (el.innerText || el.textContent || '') : '');
  const ownText = el => (el ? [...el.childNodes]
    .filter(node => node.nodeType === 3)
    .map(node => norm(node.nodeValue))
    .filter(Boolean)
    .join(' ') : '');
  const numberRe = /^[+-]?\d[\d,]*(?:\.\d+)?\s*[万亿]?$/;
  const leavesOf = root => [...root.querySelectorAll('*')]
    .filter(el => el.children.length === 0 && visible(el));
  const numberLeaves = root => leavesOf(root)
    .map(el => ({el, text: textOf(el)}))
    .filter(item => numberRe.test(item.text));
  const parseCount = text => {
    const match = String(text || '').match(/([+-]?\d[\d,]*(?:\.\d+)?)\s*([万亿]?)/);
    if (!match) return null;
    const multiplier = {'': 1, '万': 10000, '亿': 100000000}[match[2] || ''];
    const numeric = Number(match[1].replace(/,/g, '')) * multiplier;
    return Number.isFinite(numeric) ? Math.round(numeric) : null;
  };
  const METRIC_LABELS = ['播放量', '主页访问量', '作品点赞', '作品分享', '作品评论', '净增粉丝'];
  const hasMetricTitle = el => METRIC_LABELS.some(label =>
    leavesOf(el).some(leaf => textOf(leaf) === label)
  );
  const findActivePanel = () => {
    const panels = [...document.querySelectorAll('[role="tabpanel"]')]
      .filter(el => el.getAttribute('aria-hidden') !== 'true');
    const withMetrics = panels.find(el => hasMetricTitle(el) && numberLeaves(el).length > 0);
    if (withMetrics) return withMetrics;
    const tab = [...document.querySelectorAll('[role="tab"]')]
      .find(el => textOf(el) === '账号总览');
    let node = tab;
    for (let i = 0; node && i < 8; i++, node = node.parentElement) {
      if (hasMetricTitle(node) && numberLeaves(node).length > 0) return node;
    }
    return null;
  };
  const metricContainer = (panel, label) => {
    const titles = leavesOf(panel).filter(el => textOf(el) === label);
    for (const title of titles) {
      let node = title.parentElement;
      for (let i = 0; node && i < 6; i++, node = node.parentElement) {
        if (numberLeaves(node).length) return {title, node};
      }
    }
    return null;
  };
  const metricValue = (panel, label) => {
    const found = metricContainer(panel, label);
    if (!found) return {value: null, delta: null, found: false};
    const numbers = numberLeaves(found.node);
    const value = numbers.length ? parseCount(numbers[0].text) : null;
    let delta = null;
    const nodeText = textOf(found.node);
    const deltaMatch = nodeText.match(/较前(?:\d+日|昨日)?\s*([+-]\d[\d,]*|\d[\d,]*)/);
    if (deltaMatch) delta = parseCount(deltaMatch[1]);
    return {value, delta, found: true};
  };
  const periodInfo = () => {
    const match = textOf(document.body).match(
      /统计周期[:：]\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*[-~至]\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})/
    );
    if (!match) return {period_text: null, period_start: null, period_end: null};
    const pad = n => String(n).padStart(2, '0');
    return {
      period_text: match[0],
      period_start: `${match[1]}-${pad(match[2])}-${pad(match[3])}`,
      period_end: `${match[4]}-${pad(match[5])}-${pad(match[6])}`
    };
  };
  const currentPeriodLabel = () => {
    const label = Object.keys({'昨日': 1, '近7日': 1, '近30日': 1})
      .find(item => textOf(document.body).includes(item));
    return label || null;
  };
"""

_READ_SCRIPT = (
    "() => {"
    + _JS_HELPERS
    + r"""
  const bodyText = textOf(document.body);
  const riskText = /(滑块验证|拖动滑块|请完成验证|安全验证|人机验证|操作频繁|访问频繁|异常流量|检测到风险)/;
  const riskElement = [...document.querySelectorAll(
    '#captcha_container, [id*="captcha"], [class*="captcha"], [data-e2e*="captcha"], [class*="verify-wrap"], [class*="verifyModal"]'
  )].some(visible);
  const loginWall = /(扫码登录|登录后查看|请先登录|手机号登录|验证码登录)/.test(bodyText);
  const panel = findActivePanel();
  const metrics = panel ? {
    account_play_count: metricValue(panel, '播放量'),
    profile_visit_count_period: metricValue(panel, '主页访问量'),
    like_count: metricValue(panel, '作品点赞'),
    share_count: metricValue(panel, '作品分享'),
    comment_count: metricValue(panel, '作品评论'),
    net_follower_gain: metricValue(panel, '净增粉丝')
  } : {};
  let likeTotal = null;
  const likeLabel = [...document.querySelectorAll('body *')]
    .find(el => ownText(el) === '获赞' && numberLeaves(el).length > 0);
  if (likeLabel) {
    const numbers = numberLeaves(likeLabel);
    likeTotal = numbers.length ? parseCount(numbers[0].text) : null;
  }
  let followerIndicator = null;
  const fans = document.getElementById('guide_home_fans');
  if (fans) {
    const numbers = numberLeaves(fans);
    const labelText = textOf(fans).replace(/\d[\d,.]*\s*[万亿]?/, '').trim();
    followerIndicator = {
      value: numbers.length ? parseCount(numbers[0].text) : null,
      label_text: labelText || null
    };
  }
  const metricCount = panel
    ? Object.values(metrics).filter(item => item && item.value !== null).length
    : 0;
  return {
    url: location.href,
    risk_control_seen: riskElement || riskText.test(bodyText),
    login_wall: loginWall,
    panel_found: Boolean(panel),
    metric_count: metricCount,
    metrics,
    like_total: likeTotal,
    follower_indicator: followerIndicator,
    current_period_label: currentPeriodLabel(),
    ...periodInfo(),
    page_text_head: bodyText.slice(0, 300)
  };
}
"""
)

_SELECT_PERIOD_SCRIPT = r"""
(label) => {
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    if (window.getComputedStyle) {
      const style = window.getComputedStyle(el);
      if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    }
    return true;
  };
  const textOf = el => norm(el ? (el.innerText || el.textContent || '') : '');
  const ownText = el => (el ? [...el.childNodes]
    .filter(node => node.nodeType === 3)
    .map(node => norm(node.nodeValue))
    .filter(Boolean)
    .join(' ') : '');
  const combobox = [...document.querySelectorAll('[role="combobox"]')]
    .find(el => visible(el) && textOf(el).includes('时间'));
  if (!combobox) return {clicked: false, option_found: false, reason: 'period_control_not_found'};
  combobox.click();
  const options = [...document.querySelectorAll('body *')]
    .filter(el => visible(el) && el.children.length === 0 && textOf(el) === label);
  if (!options.length) return {clicked: true, option_found: false, reason: 'period_option_not_found'};
  options[options.length - 1].click();
  return {clicked: true, option_found: true, reason: null};
}
"""


def read_account_overview(page: Any) -> dict[str, Any]:
    raw = page.evaluate(_READ_SCRIPT)
    return raw if isinstance(raw, dict) else {}


def wait_for_account_overview(
    page: Any,
    *,
    settle_ms: int = 12000,
    timeout_ms: int = 20000,
    poll_ms: int = 250,
) -> dict[str, Any]:
    """Signal-based wait: overview panel with >=1 numeric metric.

    ``networkidle`` is deliberately not used: the page lazy-renders the panel
    and network silence does not imply the DOM is ready. ``settle_ms`` is only a
    fallback pause after the first empty read.
    """

    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    observed = read_account_overview(page)
    if bool(observed.get("panel_found")) and int(observed.get("metric_count") or 0) > 0:
        observed["waited_ms"] = 0
        return observed
    if callable(wait_for_timeout) and settle_ms > 0:
        wait_for_timeout(settle_ms)
    elapsed = settle_ms
    observed = read_account_overview(page)
    while elapsed < timeout_ms:
        if observed.get("risk_control_seen") or observed.get("login_wall"):
            break
        if bool(observed.get("panel_found")) and int(observed.get("metric_count") or 0) > 0:
            break
        if not callable(wait_for_timeout):
            break
        wait_for_timeout(poll_ms)
        elapsed += poll_ms
        observed = read_account_overview(page)
    observed["waited_ms"] = elapsed
    return observed


def select_period(
    page: Any,
    period: str,
    *,
    timeout_ms: int = 5000,
    poll_ms: int = 200,
) -> dict[str, Any]:
    """Switch the "时间" selector; returns selected/actual label, never retries."""

    label = PERIOD_LABELS[period]
    observed = read_account_overview(page)
    if str(observed.get("current_period_label") or "") == label:
        return {"selected": True, "actual_label": label, "reason": "already_selected"}
    attempt = page.evaluate(_SELECT_PERIOD_SCRIPT, label)
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    elapsed = 0
    while elapsed < timeout_ms:
        if callable(wait_for_timeout):
            wait_for_timeout(poll_ms)
            elapsed += poll_ms
        observed = read_account_overview(page)
        if str(observed.get("current_period_label") or "") == label:
            return {"selected": True, "actual_label": label, "reason": None}
        if not attempt.get("option_found"):
            break
    return {
        "selected": False,
        "actual_label": observed.get("current_period_label"),
        "reason": (attempt or {}).get("reason") or "period_not_switchable",
    }


def decide_state(observed: dict[str, Any]) -> str:
    if observed.get("risk_control_seen"):
        return STATE_RISK_CONTROL
    if observed.get("login_wall"):
        return STATE_LOGIN_REQUIRED
    if bool(observed.get("panel_found")) and int(observed.get("metric_count") or 0) > 0:
        return STATE_RENDERED
    return STATE_NOT_RENDERED


_PERIOD_TEXT_RE = re.compile(
    r"统计周期[:：]\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*[-~至]\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)


def parse_period_text(text: str) -> tuple[str | None, str | None]:
    """Parse ``统计周期：YYYY.MM.DD-YYYY.MM.DD`` into ISO dates."""

    match = _PERIOD_TEXT_RE.search(str(text or ""))
    if not match:
        return None, None
    pad = lambda value: f"{int(value):02d}"  # noqa: E731
    return (
        f"{match.group(1)}-{pad(match.group(2))}-{pad(match.group(3))}",
        f"{match.group(4)}-{pad(match.group(5))}-{pad(match.group(6))}",
    )
