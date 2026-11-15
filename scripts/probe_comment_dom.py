#!/usr/bin/env python3
"""Probe the real Douyin comment panel with a local logged-in browser profile.

This is a diagnostic tool for a real login session. It never retries a risk
control prompt, never opens concurrent tabs, and processes one video at a time.

Usage::

    PYENV_VERSION=3.11.11 .venv/bin/python scripts/probe_comment_dom.py \
        --source both --video-id recent --recent-limit 2 --headed

    # explicit video, custom profile, custom settle time
    .venv/bin/python scripts/probe_comment_dom.py \
        --source public --video-id 7000000000000000000 \
        --profile ./data/browser-profile --out .botharness/comment-probe/run1

Artifacts per run: page HTML, panel HTML (when found), screenshots, and a
structured JSON (`probe-*.json`) whose `signals` are strictly observed values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from douyin_creator_mcp.browser.comment_panel import (  # noqa: E402
    comment_trigger_summary,
    legacy_selector_counts,
    open_comment_panel,
    panel_outer_html,
    read_comment_items,
    read_page_signals,
)
from douyin_creator_mcp.browser.extractors import collect_all_video_cards  # noqa: E402
from douyin_creator_mcp.browser.session import BrowserSession  # noqa: E402
from douyin_creator_mcp.compliance import (  # noqa: E402
    require_platform_risk_acknowledgement,
)
from douyin_creator_mcp.config import load_settings  # noqa: E402
from douyin_creator_mcp.errors import AppError  # noqa: E402

LEGACY_SELECTORS = [
    '[data-e2e="comment-list"]',
    '[data-e2e="comment-item"]',
    '[class*="comment-list"]',
    '[class*="commentItem"]',
    '[class*="comment-item"]',
    '[class*="comment"]',
    '[id*="comment"]',
    '[class*="comment-mainContent"]',
]
CREATOR_CARD_SELECTOR = '[class*="video-card-content-"]'


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _write_text(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


class Probe:
    def __init__(self, args: argparse.Namespace, out_dir: Path) -> None:
        self.args = args
        self.out_dir = out_dir
        self.steps: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []
        self.risk_seen = False

    # Capture helpers --------------------------------------------------

    def capture(self, page: Any, prefix: str) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        html = page.content()
        artifacts["html"] = _write_text(
            self.out_dir / f"{prefix}-page.html", html
        )
        screenshot_path = self.out_dir / f"{prefix}-page.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
            artifacts["screenshot"] = str(screenshot_path)
        except Exception as exc:  # pragma: no cover - browser dependent
            artifacts["screenshot_error"] = str(exc)
        panel_html = panel_outer_html(page)
        if panel_html:
            artifacts["panel_html"] = _write_text(
                self.out_dir / f"{prefix}-panel.html", panel_html
            )
        return artifacts

    def step(self, page: Any, action: str, started: float, **extra: Any) -> None:
        record = {
            "action": action,
            "url": str(getattr(page, "url", "") or ""),
            "ms": int((time.monotonic() - started) * 1000),
            "hash": _sha1(page.content()),
        }
        record.update(extra)
        self.steps.append(record)

    def attempt(self, record: dict[str, Any]) -> None:
        self.attempts.append(record)

    # Navigation -------------------------------------------------------

    def goto(self, page: Any, url: str, action: str) -> None:
        started = time.monotonic()
        page.goto(url, wait_until="domcontentloaded")
        if self.args.settle > 0:
            page.wait_for_timeout(self.args.settle)
        self.step(page, action, started, requested_url=url)

    # Panel opening ----------------------------------------------------

    def open_panel(self, page: Any) -> dict[str, Any]:
        result = open_comment_panel(
            page,
            settle_ms=self.args.settle,
            timeout_ms=self.args.timeout_ms,
            on_attempt=self.attempt,
        )
        self.risk_seen = bool(result.get("risk_control_seen"))
        return result

    # Evidence ---------------------------------------------------------

    def evidence(
        self,
        *,
        source: str,
        video_id: str,
        page: Any,
        open_result: dict[str, Any],
        artifacts: dict[str, str],
        extra_signals: dict[str, Any] | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        observed = read_comment_items(page)
        signals_after = read_page_signals(page)
        counts = legacy_selector_counts(page, LEGACY_SELECTORS)
        strategies = [
            [str(attempt.get("strategy")), int(attempt.get("matched") or 0)]
            for attempt in self.attempts
        ]
        signals = {
            "comment_button_found": any(
                int(attempt.get("matched") or 0) > 0 for attempt in self.attempts
            ),
            "panel_opened": bool(open_result.get("opened")),
            "reply_button_seen": bool(signals_after.get("reply_button_seen")),
            "risk_control_seen": bool(signals_after.get("risk_control_seen")),
            "login_wall": bool(signals_after.get("login_wall")),
        }
        if extra_signals:
            signals.update(extra_signals)
        payload = {
            "source": source,
            "video_id": video_id,
            "route": route,
            "started_at": _now_iso(),
            "steps": self.steps,
            "signals": signals,
            "artifacts": artifacts,
            "selectors_tried": strategies + counts,
            "attempts": self.attempts,
            "panel": {
                "opened_by": open_result.get("strategy"),
                "found": bool(observed.get("panel_found")),
                "reply_anchor_count": int(observed.get("reply_anchor_count") or 0),
                "empty_seen": bool(observed.get("empty_seen")),
                "header_seen": bool(observed.get("header_seen")),
                "item_count": len(observed.get("items") or []),
                "total_comment_count": observed.get("total_comment_count"),
            },
            "final_url": str(getattr(page, "url", "") or ""),
        }
        return payload


def _resolve_recent_ids(settings: Any, limit: int) -> list[str]:
    """Prefer the local DB; the caller falls back to scraping creator cards."""

    db_path = Path(settings.data_dir) / "douyin.sqlite"
    if not db_path.is_file():
        return []
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT video_id, item_id FROM videos "
                "WHERE account_id = ? AND is_active = 1 "
                "ORDER BY publish_time DESC LIMIT ?",
                ("browser-default", limit),
            ).fetchall()
    except sqlite3.Error:
        return []
    ids: list[str] = []
    for row in rows:
        value = str(row["video_id"] or row["item_id"] or "").strip()
        if value:
            ids.append(value)
    return ids


def _creator_recent_ids(page: Any, limit: int) -> list[str]:
    try:
        records, _ = collect_all_video_cards(page, max_scrolls=3)
    except Exception:
        return []
    ids = [
        str(record.get("platform_item_id") or "").strip()
        for record in records
        if record.get("platform_item_id")
    ]
    return ids[:limit]


def run_public(
    probe: Probe,
    page: Any,
    settings: Any,
    video_id: str,
) -> dict[str, Any]:
    url = settings.douyin_web_video_url_template.format(video_id=video_id)
    probe.goto(page, url, f"goto_public_{video_id}")
    signals = read_page_signals(page)
    open_result: dict[str, Any] = {
        "opened": False,
        "strategy": None,
        "attempts": [],
        "signals": signals,
        "risk_control_seen": bool(signals.get("risk_control_seen")),
    }
    if signals.get("risk_control_seen") or signals.get("login_wall"):
        probe.risk_seen = bool(signals.get("risk_control_seen"))
    else:
        open_result = probe.open_panel(page)
    artifacts = probe.capture(page, f"public-{video_id}")
    return probe.evidence(
        source="public",
        video_id=video_id,
        page=page,
        open_result=open_result,
        artifacts=artifacts,
        route="public_video_modal",
    )


def _find_card(page: Any, video_id: str) -> Any | None:
    cards = page.locator(CREATOR_CARD_SELECTOR)
    count = cards.count()
    for index in range(count):
        card = cards.nth(index)
        try:
            html = card.evaluate("el => el.outerHTML")
        except Exception:
            continue
        if video_id and video_id in str(html):
            return card
    return None


def _click_creator_card_comment(page: Any, card: Any) -> dict[str, Any]:
    """Click the "评论" metric/entry inside one creator card (one attempt set)."""

    return card.evaluate(
        r"""
        (root) => {
          const norm = value => (value || '').replace(/\s+/g, ' ').trim();
          const visible = el => {
            if (!el) return false;
            if (window.getComputedStyle) {
              const style = window.getComputedStyle(el);
              if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
            }
            return true;
          };
          const describe = el => ({
            tag: (el.tagName || '').toLowerCase(),
            text: norm(el.innerText || el.textContent).slice(0, 120),
            aria_label: (el.getAttribute && el.getAttribute('aria-label')) || '',
            outer_html: (el.outerHTML || '').slice(0, 600)
          });
          const all = [...root.querySelectorAll('*')].filter(visible);
          const exact = all.filter(el => norm(el.innerText) === '评论');
          const containing = all.filter(el => {
            const text = norm(el.innerText);
            return text && text.length <= 12 && /评论/.test(text) && el.children.length === 0;
          });
          const candidates = (exact.length ? exact : containing).slice(0, 6);
          let clicked = false;
          let clicked_info = candidates.length ? describe(candidates[0]) : null;
          if (candidates.length) {
            let target = candidates[0];
            for (let depth = 0; target && depth < 4; depth++, target = target.parentElement) {
              if (!target) break;
              const tag = (target.tagName || '').toLowerCase();
              const style = window.getComputedStyle ? window.getComputedStyle(target) : null;
              if (tag === 'button' || tag === 'a' || (style && style.cursor === 'pointer')) break;
            }
            if (!target) target = candidates[0];
            clicked_info = describe(target);
            try {
              target.click();
              clicked = true;
            } catch (error) {
              clicked = false;
            }
          }
          return {
            strategy: 'creator_card_comment',
            matched: candidates.length,
            clicked: clicked,
            clicked_info: clicked_info,
            candidates: candidates.map(describe)
          };
        }
        """
    )


def _click_text(page: Any, texts: list[str]) -> dict[str, Any]:
    return page.evaluate(
        r"""
        (texts) => {
          const norm = value => (value || '').replace(/\s+/g, ' ').trim();
          const all = [...document.querySelectorAll('body *')].filter(el => {
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            return !style || (style.display !== 'none' && style.visibility !== 'hidden');
          });
          for (const text of texts) {
            const matches = all.filter(el => norm(el.innerText) === text);
            if (!matches.length) continue;
            const target = matches[0];
            let clickable = target;
            for (let depth = 0; clickable && depth < 4; depth++, clickable = clickable.parentElement) {
              if (!clickable) break;
              const tag = (clickable.tagName || '').toLowerCase();
              const style = window.getComputedStyle ? window.getComputedStyle(clickable) : null;
              if (tag === 'button' || tag === 'a' || (style && style.cursor === 'pointer')) break;
            }
            try {
              (clickable || target).click();
              return {strategy: 'text:' + text, matched: matches.length, clicked: true};
            } catch (error) {
              return {strategy: 'text:' + text, matched: matches.length, clicked: false};
            }
          }
          return {strategy: 'text:' + texts.join('|'), matched: 0, clicked: false};
        }
        """,
        texts,
    )


def run_creator(
    probe: Probe,
    page: Any,
    settings: Any,
    video_id: str,
) -> dict[str, Any]:
    probe.goto(page, settings.douyin_creator_video_url, f"goto_creator_{video_id}")
    signals = read_page_signals(page)
    artifacts = probe.capture(page, f"creator-{video_id}-list")
    base = {
        "source": "creator",
        "video_id": video_id,
        "page": page,
        "artifacts": artifacts,
        "extra_signals": {"creator_card_found": False},
    }
    if signals.get("risk_control_seen") or signals.get("login_wall"):
        return probe.evidence(
            open_result={
                "opened": False,
                "strategy": None,
                "attempts": [],
                "signals": signals,
                "risk_control_seen": bool(signals.get("risk_control_seen")),
            },
            route="creator_video_manage",
            **base,
        )

    route_attempts: list[dict[str, Any]] = []
    card = _find_card(page, video_id)
    open_result: dict[str, Any] = {
        "opened": False,
        "strategy": None,
        "attempts": [],
        "signals": signals,
        "risk_control_seen": False,
    }
    if card is not None:
        card_attempt = _click_creator_card_comment(page, card)
        card_attempt["strategy"] = "creator_card_comment"
        probe.attempt(card_attempt)
        route_attempts.append(
            {"route": "creator_card_comment", "clicked": card_attempt.get("clicked"),
             "matched": card_attempt.get("matched")}
        )
        if card_attempt.get("clicked"):
            open_result = probe.open_panel(page)
            if open_result.get("opened") or open_result.get("risk_control_seen"):
                route_attempts[-1]["panel_opened"] = bool(open_result.get("opened"))
    else:
        route_attempts.append({"route": "creator_card_comment", "clicked": False, "matched": 0})

    if not open_result.get("opened") and not probe.risk_seen:
        # Speculative nav route: 互动管理 -> 评论管理 (logged as an attempt).
        nav = _click_text(page, ["互动管理", "互动", "评论管理"])
        nav["strategy"] = "creator_interaction_nav"
        probe.attempt(nav)
        route_attempts.append(
            {"route": "creator_interaction_nav", "clicked": nav.get("clicked"),
             "matched": nav.get("matched")}
        )
        if nav.get("clicked"):
            page.wait_for_timeout(max(600, probe.args.settle))
            probe.step(page, "creator_interaction_nav", time.monotonic())
            open_result = probe.open_panel(page)

    artifacts = probe.capture(page, f"creator-{video_id}-after")
    evidence = probe.evidence(
        source="creator",
        video_id=video_id,
        page=page,
        open_result=open_result,
        artifacts=artifacts,
        route="creator_video_manage",
        extra_signals={"creator_card_found": card is not None},
        **{key: value for key, value in base.items() if key not in {"source", "video_id", "page", "artifacts", "extra_signals"}},
    )
    evidence["route_attempts"] = route_attempts
    return evidence


def _print_compare(evidence: list[dict[str, Any]]) -> None:
    print("\n=== probe summary ===")
    for item in evidence:
        signals = item.get("signals") or {}
        panel = item.get("panel") or {}
        print(
            f"[{item['source']}] video={item['video_id']} "
            f"button={signals.get('comment_button_found')} "
            f"opened={signals.get('panel_opened')} "
            f"reply={signals.get('reply_button_seen')} "
            f"risk={signals.get('risk_control_seen')} "
            f"login={signals.get('login_wall')} "
            f"items={panel.get('item_count')} "
            f"total={panel.get('total_comment_count')}"
        )
        print(f"    json: {item.get('_json_path')}")
    if len(evidence) >= 2:
        public_ok = [e for e in evidence if e["source"] == "public" and (e.get("panel") or {}).get("item_count")]
        creator_ok = [e for e in evidence if e["source"] == "creator" and (e.get("panel") or {}).get("item_count")]
        print(
            f"\n结论参考：public 抓到评论的用例 {len(public_ok)} 个，"
            f"creator 抓到评论的用例 {len(creator_ok)} 个。"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["public", "creator", "both"], default="both")
    parser.add_argument("--video-id", default="recent", help="作品 id，或 recent")
    parser.add_argument("--recent-limit", type=int, default=2)
    parser.add_argument("--profile", default=None, help="浏览器 profile 目录")
    parser.add_argument("--out", default=None, help="证据输出目录")
    parser.add_argument("--settle", type=int, default=None, help="等待毫秒数")
    parser.add_argument("--timeout-ms", type=int, default=8000, help="面板信号等待上限")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--headed", action="store_true", default=True)
    group.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.profile:
        settings.douyin_browser_profile_dir = Path(args.profile)
    settings.douyin_browser_headless = bool(args.headless)
    if args.settle is not None:
        settings.douyin_comment_settle_ms = max(0, args.settle)
    args.settle = int(settings.douyin_comment_settle_ms)

    try:
        require_platform_risk_acknowledgement(settings.data_dir)
    except AppError as exc:
        print(exc.message)
        print("先运行：douyin-mcp acknowledge-platform-risk --yes")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else Path(".botharness") / "comment-probe" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = ["public", "creator"] if args.source == "both" else [args.source]
    evidence: list[dict[str, Any]] = []
    session = BrowserSession(settings, headless=settings.douyin_browser_headless)
    try:
        page = (
            session.open_creator_video_page()
            if args.video_id == "recent"
            else None
        )
        if page is None:
            context = session.start()
            pages = list(getattr(context, "pages", None) or [])
            page = pages[0] if pages else context.new_page()
        if args.video_id == "recent":
            video_ids = _resolve_recent_ids(settings, args.recent_limit)
            if not video_ids:
                video_ids = _creator_recent_ids(page, args.recent_limit)
            if not video_ids:
                print("未能解析 recent 作品（本地 DB 与创作者作品页都没有可用 id）。")
                return 3
        else:
            video_ids = [args.video_id]

        stop = False
        for source in sources:
            for video_id in video_ids:
                if stop:
                    break
                probe = Probe(args, out_dir)
                if source == "public":
                    result = run_public(probe, page, settings, video_id)
                else:
                    result = run_creator(probe, page, settings, video_id)
                json_path = out_dir / f"probe-{source}-{video_id}.json"
                result["_json_path"] = str(json_path)
                _write_text(
                    json_path,
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                )
                evidence.append(result)
                if probe.risk_seen:
                    print("检测到验证/风控，已停止后续探路并落盘当前证据。")
                    stop = True
                    break
    finally:
        session.close()

    compare_path = out_dir / "compare.json"
    _write_text(
        compare_path,
        json.dumps(
            {"generated_at": _now_iso(), "runs": [
                {key: value for key, value in item.items() if key != "attempts"}
                for item in evidence
            ]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    _print_compare(evidence)
    print(f"\nall artifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
