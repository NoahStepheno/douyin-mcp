"""Open and read the visible comment panel using semantic UI anchors.

Douyin's public video page (``/video/<id>`` -> ``/jingxuan?modal_id=<id>``) renders
an info feed plus a video layer; the comment drawer is **closed by default**, so
the DOM contains no comment nodes until the panel is opened. This module owns:

1. finding a comment trigger (right action bar count, ``aria-label``, exact
   "评论" tab) and opening the drawer;
2. waiting for a positive "comments rendered" signal (a "回复" action exists);
3. reading rendered comment items anchored on the Chinese "回复"/"展开 N 条回复"
   actions, walking up to the item container. No hashed class names and no
   ``data-e2e`` hooks are required for the semantic path.

The module is shared by the collector (``browser/comments.py``) and the probe
script (``scripts/probe_comment_dom.py``) so evidence and production follow the
same code path.
"""

from __future__ import annotations

from typing import Any, Callable

#: Strategies tried in order. Each name is recorded in observation/probe output.
OPEN_STRATEGIES = ("count_button", "aria_label", "exact_tab")

_JS_HELPERS = r"""
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const textOf = el => norm(el ? (el.innerText || '') : '');
  const visible = el => {
    if (!el) return false;
    if (el.getAttribute && el.getAttribute('aria-hidden') === 'true') return false;
    if (window.getComputedStyle) {
      const style = window.getComputedStyle(el);
      if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    }
    return true;
  };
  const boxOf = el => {
    const rect = el.getBoundingClientRect();
    return {
      x: Math.round(rect.x), y: Math.round(rect.y),
      width: Math.round(rect.width), height: Math.round(rect.height)
    };
  };
  const areaOf = el => {
    const rect = el.getBoundingClientRect();
    return rect.width * rect.height;
  };
  const describe = (el, strategy) => ({
    strategy: strategy,
    tag: el.tagName ? el.tagName.toLowerCase() : '',
    role: el.getAttribute ? (el.getAttribute('role') || '') : '',
    aria_label: el.getAttribute ? (el.getAttribute('aria-label') || '') : '',
    text: textOf(el).slice(0, 120),
    box: boxOf(el),
    outer_html: (el.outerHTML || '').slice(0, 600)
  });
  const timeRe = /^(刚刚|\d+\s*(秒|分钟|小时|天|周|个月|年)前|(今天|昨天|前天)\s*\d{1,2}:\d{2}|\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2})?|\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(\s+\d{1,2}:\d{2})?|\d{1,2}:\d{2})$/;
  const countRe = /^\d+(?:\.\d+)?\s*[万亿]?$/;
  const replyTextRe = /^(回复|展开\s*\d+\s*条回复|\d+\s*条回复)$/;
  const isReplyAnchor = el => {
    if (!el || !visible(el)) return false;
    const text = textOf(el);
    if (!text || text.length > 12 || !replyTextRe.test(text)) return false;
    return ![...el.children].some(child => {
      const childText = textOf(child);
      return childText && childText.length <= 12 && replyTextRe.test(childText);
    });
  };
  const replyAnchors = () =>
    [...document.querySelectorAll('body *')].filter(isReplyAnchor);
  const findPanel = anchors => {
    const minimum = Math.min(anchors.length, 2);
    for (const anchor of anchors) {
      let node = anchor.parentElement;
      for (let depth = 0; node && depth < 16; depth++, node = node.parentElement) {
        if (!node || !visible(node)) break;
        const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
        const scrollable = style && /(auto|scroll)/.test(style.overflowY || '');
        const inside = anchors.filter(item => node.contains(item)).length;
        if (scrollable && node.clientHeight > 150 && inside >= minimum) return node;
      }
    }
    if (anchors.length) {
      let node = anchors[0].parentElement;
      for (let depth = 0; node && depth < 16; depth++, node = node.parentElement) {
        if (!node) break;
        const inside = anchors.filter(item => node.contains(item)).length;
        if (inside === anchors.length && /条评论|全部评论/.test(textOf(node))) {
          return node;
        }
      }
    }
    return null;
  };
"""

_SIGNALS_SCRIPT = (
    "() => {"
    + _JS_HELPERS
    + r"""
  const bodyText = textOf(document.body);
  const riskText = /(滑块验证|拖动滑块|请完成验证|安全验证|人机验证|操作频繁|访问频繁|异常流量|检测到风险)/;
  const loginText = /(扫码登录|登录后查看|请先登录|手机号登录|验证码登录|登录后参与评论)/;
  const emptyText = /(暂无评论|还没有评论|来抢首评|暂时没有评论|还没有人评论)/;
  const disabledText = /(评论已关闭|作者已关闭评论|评论功能已关闭|已关闭评论|该作品评论已关闭)/;
  const notPublicUrl = /(web_video_404_link|video_404|\/video\/404)/;
  const notPublicText = /(作品不存在|视频不存在|内容不存在|作品已删除|视频已删除|该作品不可见)/;
  const riskSelectors = [
    '#captcha_container', '[id*="captcha"]', '[class*="captcha"]',
    '[data-e2e*="captcha"]', '[class*="verify-wrap"]', '[class*="verifyModal"]'
  ];
  const riskElement = riskSelectors.some(sel => {
    try { return [...document.querySelectorAll(sel)].some(visible); } catch (error) { return false; }
  });
  const anchors = replyAnchors();
  const panel = findPanel(anchors);
  return {
    url: location.href,
    risk_control_seen: riskElement || riskText.test(bodyText),
    login_wall: loginText.test(bodyText),
    empty_seen: emptyText.test(bodyText),
    comments_disabled_seen: disabledText.test(bodyText),
    video_not_public_seen: notPublicUrl.test(location.href) || notPublicText.test(bodyText),
    reply_button_seen: anchors.length > 0,
    reply_anchor_count: anchors.length,
    header_seen: /条评论|全部评论/.test(bodyText.slice(0, 8000)),
    panel_found: Boolean(panel),
    panel_text: panel ? textOf(panel).slice(0, 300) : '',
    page_text_head: bodyText.slice(0, 400)
  };
}
"""
)

_TRIGGER_SCRIPT = (
    "(strategy) => {"
    + _JS_HELPERS
    + r"""
  const all = [...document.querySelectorAll('body *')]
    .filter(el => visible(el) && areaOf(el) >= 64);
  let pool = [];
  if (strategy === 'count_button') {
    pool = all.filter(el => {
      const text = textOf(el);
      if (!text || text.length > 28) return false;
      if (!/评论/.test(text)) return false;
      if (!/\d/.test(text)) return false;
      return boxOf(el).x > window.innerWidth * 0.45;
    });
  } else if (strategy === 'aria_label') {
    pool = all.filter(el => (el.getAttribute('aria-label') || '').includes('评论'));
  } else if (strategy === 'exact_tab') {
    pool = all.filter(el => textOf(el) === '评论');
  }
  pool.sort((a, b) => areaOf(a) - areaOf(b));
  const candidates = pool.slice(0, 8).map(el => describe(el, strategy));
  let clicked = false;
  let clicked_info = candidates.length ? candidates[0] : null;
  if (pool.length) {
    let target = pool[0];
    for (let depth = 0; target && depth < 5; depth++, target = target.parentElement) {
      if (!target) break;
      const tag = (target.tagName || '').toLowerCase();
      if (tag === 'button' || tag === 'a' || target.getAttribute('role') === 'button') break;
      const style = window.getComputedStyle ? window.getComputedStyle(target) : null;
      if (style && style.cursor === 'pointer') break;
    }
    if (!target) target = pool[0];
    clicked_info = describe(target, strategy);
    try {
      target.click();
      clicked = true;
    } catch (error) {
      clicked = false;
    }
  }
  return {
    strategy: strategy,
    matched: pool.length,
    clicked: clicked,
    clicked_info: clicked_info,
    candidates: candidates
  };
}
"""
)

_EXTRACT_ITEMS_SCRIPT = (
    "() => {"
    + _JS_HELPERS
    + r"""
  // Fast scope when the stable data-e2e hook exists, semantic anchors otherwise.
  const listScope = document.querySelector('[data-e2e="comment-list"]') || document;
  const scopeAnchors = [...listScope.querySelectorAll('*')].filter(isReplyAnchor);
  const anchors = scopeAnchors.length ? scopeAnchors : replyAnchors();

  const timeScopeRe = /^(刚刚|\d+\s*(秒|分钟|小时|天|周|个月|年)前(\s*[·•]\s*\S+)?|(今天|昨天|前天)\s*\d{1,2}:\d{2}(\s*[·•]\s*\S+)?|\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2})?|\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(\s+\d{1,2}:\d{2})?|\d{1,2}:\d{2})$/;
  const tagsOf = item => [...new Set(
    [...item.querySelectorAll('[class*="comment-item-tag"]')].map(el => textOf(el)).filter(Boolean)
  )];
  const nicknameOf = item => {
    const anchor = item.querySelector('a[href*="/user/"]');
    if (!anchor) return '';
    const clone = anchor.cloneNode(true);
    clone.querySelectorAll('[class*="comment-item-tag"], [class*="badge"]').forEach(el => el.remove());
    return textOf(clone);
  };
  const stripNoise = text => norm(
    String(text || '')
      .replace(/作者赞过/g, ' ')
      .replace(/^(置顶|作者)\s*/g, '')
      .replace(/\s*(置顶|作者赞过)\s*$/g, '')
  );

  const findContainer = anchor => {
    let node = anchor.parentElement;
    let fallback = null;
    for (let depth = 0; node && depth < 12; depth++, node = node.parentElement) {
      if (!node || !visible(node)) break;
      const leaves = [...node.querySelectorAll('*')].filter(el => el.children.length === 0);
      const inside = anchors.filter(item => node.contains(item)).length;
      const hasTime = leaves.some(el => timeScopeRe.test(textOf(el)));
      const userLinks = node.querySelectorAll('a[href*="/user/"]').length;
      const text = textOf(node);
      if (inside === 1 && hasTime && userLinks >= 1 && text.length <= 3000) {
        return node;
      }
      if (text.length >= 12 && text.length <= 3000) fallback = node;
      if (inside > 1) break;
    }
    return fallback;
  };

  const containers = [];
  for (const anchor of anchors) {
    const node = findContainer(anchor);
    if (node && !containers.includes(node)) containers.push(node);
  }
  const itemContainers = containers.filter(node =>
    !containers.some(other => other !== node && node.contains(other))
  );

  const items = itemContainers.map(node => {
    const nickname = nicknameOf(node);
    const leaves = [...node.querySelectorAll('*')].filter(el => el.children.length === 0);
    const timeEl = leaves.find(el => timeScopeRe.test(textOf(el))) || null;
    const timeText = timeEl ? textOf(timeEl) : '';
    let content = '';
    if (timeEl) {
      let block = timeEl;
      if (block.parentElement && block.parentElement.children.length === 1 &&
          !timeScopeRe.test(textOf(block.parentElement))) {
        block = block.parentElement;
      }
      let sib = block.previousElementSibling;
      while (sib && !content) {
        const text = textOf(sib);
        if (text && !/^(作者|置顶)$/.test(text) && !/^(回复|分享)$/.test(text)) content = text;
        sib = sib.previousElementSibling;
      }
    }
    content = stripNoise(content);
    if (nickname && content.startsWith(nickname)) content = content.slice(nickname.length).trim();
    if (!content) {
      const candidates = leaves
        .map(el => stripNoise(textOf(el)))
        .filter(text => text && text !== nickname && !timeScopeRe.test(text) &&
          !/^(回复|分享|\d+|置顶|作者)$/.test(text) && !/互相关注/.test(text));
      candidates.sort((a, b) => b.length - a.length);
      content = candidates[0] || '';
    }
    const stats = node.querySelector('[class*="comment-item-stats-container"]');
    const likeText = stats ? textOf(stats.querySelector('p')) : '';
    const tags = tagsOf(node);
    const replyEl = leaves.find(el => /^(回复|展开\s*\d+\s*条回复|\d+\s*条回复)$/.test(textOf(el)));
    return {
      comment_id: '',
      content: content,
      nickname: nickname,
      like_text: likeText,
      reply_text: replyEl ? textOf(replyEl) : '',
      time_text: timeText,
      is_pinned: /(^|\s)置顶/.test(textOf(node)),
      is_author_reply: tags.includes('作者')
    };
  });

  const bodyText = textOf(document.body);
  const panel = findPanel(anchors);
  let headerText = '';
  const list = document.querySelector('[data-e2e="comment-list"]');
  if (list && list.parentElement) {
    const siblings = [...list.parentElement.children];
    const index = siblings.indexOf(list);
    headerText = siblings.slice(Math.max(0, index - 3), index).map(el => textOf(el)).join(' ');
  }
  // Only the header block is a trustworthy total source; never comment bodies.
  if (!headerText.trim() && panel) {
    const firstItem = panel.querySelector('[data-e2e="comment-item"]');
    const panelText = textOf(panel);
    headerText = firstItem ? '' : panelText.slice(0, 300);
  }
  const countSource = headerText.trim();
  const endText = /(没有更多评论|已显示全部评论|到底了|暂无更多评论)/;
  const moreText = /(展开更多|查看更多评论|加载更多评论|查看全部评论)/;
  return {
    reply_anchor_count: anchors.length,
    panel_found: Boolean(panel) || Boolean(list),
    panel_text: panel ? textOf(panel).slice(0, 300) : '',
    header_text: countSource.slice(0, 1200),
    empty_seen: /(暂无评论|还没有评论|来抢首评|暂时没有评论|还没有人评论)/.test(bodyText),
    comments_disabled_seen: /(评论已关闭|作者已关闭评论|评论功能已关闭|已关闭评论|该作品评论已关闭)/.test(bodyText),
    has_more_hint: endText.test(bodyText) ? false : (moreText.test(bodyText) ? true : null),
    items: items
  };
}
"""
)

_SCROLL_SCRIPT = (
    "() => {"
    + _JS_HELPERS
    + r"""
  const anchors = replyAnchors();
  const panel = findPanel(anchors);
  if (panel && panel.scrollHeight > panel.clientHeight + 4) {
    panel.scrollTop = panel.scrollHeight;
    return 'panel';
  }
  window.scrollTo(0, document.documentElement.scrollHeight);
  return 'window';
}
"""
)

_LEGACY_SELECTOR_COUNTS_SCRIPT = r"""
(selectors) => selectors.map(selector => {
  try {
    return [selector, document.querySelectorAll(selector).length];
  } catch (error) {
    return [selector, -1];
  }
})
"""


def _signals(page: Any) -> dict[str, Any]:
    raw = page.evaluate(_SIGNALS_SCRIPT)
    return raw if isinstance(raw, dict) else {}


def read_page_signals(page: Any) -> dict[str, Any]:
    """Return current risk/login/empty/panel signals without clicking."""

    return _signals(page)


def read_comment_items(page: Any) -> dict[str, Any]:
    """Read rendered comments using "回复" anchors (no hashed class names)."""

    raw = page.evaluate(_EXTRACT_ITEMS_SCRIPT)
    return raw if isinstance(raw, dict) else {}


def scroll_comment_panel(page: Any) -> str:
    return str(page.evaluate(_SCROLL_SCRIPT))


def legacy_selector_counts(page: Any, selectors: list[str]) -> list[list[Any]]:
    """Diagnostics: prove which legacy/hook selectors hit (usually 0)."""

    raw = page.evaluate(_LEGACY_SELECTOR_COUNTS_SCRIPT, selectors)
    if not isinstance(raw, list):
        return []
    return [[str(item[0]), int(item[1])] for item in raw if isinstance(item, list) and len(item) == 2]


def try_open_strategy(page: Any, strategy: str) -> dict[str, Any]:
    raw = page.evaluate(_TRIGGER_SCRIPT, strategy)
    return raw if isinstance(raw, dict) else {"strategy": strategy, "matched": 0, "clicked": False}


def wait_for_comment_signal(
    page: Any,
    *,
    settle_ms: int = 600,
    timeout_ms: int = 8000,
    poll_ms: int = 250,
) -> dict[str, Any]:
    """Wait for "回复" anchors (positive render signal) or an explicit empty state."""

    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if callable(wait_for_timeout) and settle_ms > 0:
        wait_for_timeout(settle_ms)
    elapsed = 0
    signals = _signals(page)
    while elapsed < timeout_ms:
        if signals.get("risk_control_seen") or signals.get("login_wall"):
            return signals
        if signals.get("reply_button_seen") or signals.get("empty_seen"):
            return signals
        if not callable(wait_for_timeout):
            break
        wait_for_timeout(poll_ms)
        elapsed += poll_ms
        signals = _signals(page)
    return signals


def open_comment_panel(
    page: Any,
    *,
    settle_ms: int = 600,
    timeout_ms: int = 8000,
    strategies: tuple[str, ...] = OPEN_STRATEGIES,
    on_attempt: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Try each semantic trigger once; never retry a risk-control prompt.

    Returns ``{opened, strategy, attempts, signals, risk_control_seen}``. A risk
    or login signal aborts the strategy loop immediately.
    """

    attempts: list[dict[str, Any]] = []
    opened = False
    used_strategy: str | None = None
    signals = _signals(page)
    if signals.get("risk_control_seen") or signals.get("login_wall"):
        return {
            "opened": False,
            "strategy": None,
            "attempts": attempts,
            "signals": signals,
            "risk_control_seen": bool(signals.get("risk_control_seen")),
        }
    if signals.get("reply_button_seen") or signals.get("empty_seen"):
        return {
            "opened": True,
            "strategy": "already_open",
            "attempts": attempts,
            "signals": signals,
            "risk_control_seen": False,
        }
    for strategy in strategies:
        attempt = try_open_strategy(page, strategy)
        attempt["signal"] = wait_for_comment_signal(
            page, settle_ms=settle_ms, timeout_ms=timeout_ms
        )
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)
        signals = attempt["signal"]
        if signals.get("risk_control_seen") or signals.get("login_wall"):
            break
        if signals.get("reply_button_seen") or signals.get("empty_seen"):
            opened = True
            used_strategy = strategy
            break
    return {
        "opened": opened,
        "strategy": used_strategy,
        "attempts": attempts,
        "signals": signals,
        "risk_control_seen": bool(signals.get("risk_control_seen")),
    }


def panel_outer_html(page: Any, max_chars: int = 2_000_000) -> str | None:
    raw = page.evaluate(
        "() => {"
        + _JS_HELPERS
        + r"""
  const anchors = replyAnchors();
  const panel = findPanel(anchors);
  return panel ? panel.outerHTML.slice(0, 2000000) : null;
}
"""
    )
    if not isinstance(raw, str) or not raw:
        return None
    return raw[:max_chars]


def comment_trigger_summary(page: Any) -> list[dict[str, Any]]:
    """Probe helper: candidate counts per strategy, without clicking."""

    raw = page.evaluate(
        "() => {"
        + _JS_HELPERS
        + r"""
  const all = [...document.querySelectorAll('body *')].filter(el => visible(el) && areaOf(el) >= 64);
  const strategies = ['count_button', 'aria_label', 'exact_tab'];
  return strategies.map(strategy => {
    let pool = [];
    if (strategy === 'count_button') {
      pool = all.filter(el => {
        const text = textOf(el);
        return text && text.length <= 28 && /评论/.test(text) && /\d/.test(text) &&
          boxOf(el).x > window.innerWidth * 0.45;
      });
    } else if (strategy === 'aria_label') {
      pool = all.filter(el => (el.getAttribute('aria-label') || '').includes('评论'));
    } else {
      pool = all.filter(el => textOf(el) === '评论');
    }
    pool.sort((a, b) => areaOf(a) - areaOf(b));
    return {strategy: strategy, matched: pool.length, sample: pool.slice(0, 3).map(el => describe(el, strategy))};
  });
}
"""
    )
    return raw if isinstance(raw, list) else []
