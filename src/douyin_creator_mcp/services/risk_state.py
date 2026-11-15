"""Account-level risk-control cooldown and skipped-event counters (issue #6).

Both stores live under ``DATA_DIR`` and are deliberately boring JSON files so a
human can inspect or inject a cooldown state manually for acceptance, without
burning real platform quota. A corrupt/expired cooldown file never raises: it is
treated as "no cooldown" and surfaced as a warning in ``get_status``.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
RISK_COOLDOWN_FILENAME = "risk_cooldown.json"
SKIPPED_EVENTS_FILENAME = "skipped_events.json"
RISK_COOLDOWN_VERSION = 1
SKIPPED_EVENTS_VERSION = 1
DEFAULT_ACCOUNT_ID = "browser-default"
SKIPPED_WINDOW_DAYS = 7
RISK_COOLDOWN_SCHEMA: dict[str, Any] = {
    "version": 1,
    "entries": {
        "<account_id>": {
            "cooldown_until": "ISO8601 with timezone, e.g. 2026-11-16T01:30:00+08:00",
            "hours": "6 | 12 | 24",
            "created_at": "ISO8601 with timezone",
            "reason": "risk_control",
        }
    },
}


def _display_path(path: Path) -> str:
    """Prefer a repo-relative path so MCP response sanitisation keeps it usable."""

    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except (OSError, ValueError):
        return str(path)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class RiskCooldown:
    """Single-account cooldown state (not grouped by collection type)."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(data_dir) / RISK_COOLDOWN_FILENAME
        self.account_id = account_id
        self._now = now or (lambda: datetime.now(SHANGHAI))

    def activate(self, hours: int, *, reason: str = "risk_control") -> None:
        moment = self._now().astimezone(SHANGHAI).replace(microsecond=0)
        payload = {
            "version": RISK_COOLDOWN_VERSION,
            "entries": {
                self.account_id: {
                    "cooldown_until": (
                        moment + timedelta(hours=hours)
                    ).isoformat(),
                    "hours": hours,
                    "created_at": moment.isoformat(),
                    "reason": reason,
                }
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def clear(self) -> bool:
        """Manual-only clear (CLI); also removes a hand-written/invalid state."""

        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state_path": _display_path(self.path),
            "state_filename": RISK_COOLDOWN_FILENAME,
            "schema": RISK_COOLDOWN_SCHEMA,
            "account_id": self.account_id,
            "active": False,
            "cooldown_until": None,
            "remaining_seconds": 0,
            "hours": None,
            "reason": None,
            "warnings": [],
        }
        if not self.path.is_file():
            return result
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            result["warnings"].append(
                f"{RISK_COOLDOWN_FILENAME} 无法读取，已按无冷却处理。"
            )
            return result
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            result["warnings"].append(
                f"{RISK_COOLDOWN_FILENAME} 缺少 entries，已按无冷却处理。"
            )
            return result
        entry = entries.get(self.account_id)
        if entry is None and len(entries) == 1:
            entry = next(iter(entries.values()))
        if not isinstance(entry, dict):
            result["warnings"].append(
                f"{RISK_COOLDOWN_FILENAME} 缺少 {self.account_id} 条目，已按无冷却处理。"
            )
            return result
        until = _parse_iso(entry.get("cooldown_until"))
        if until is None:
            result["warnings"].append(
                f"{RISK_COOLDOWN_FILENAME} 的 cooldown_until 缺失或不是带时区 ISO8601，"
                "已按无冷却处理。"
            )
            return result
        hours = entry.get("hours")
        if hours is not None and (
            not isinstance(hours, int) or isinstance(hours, bool)
        ):
            result["warnings"].append(
                f"{RISK_COOLDOWN_FILENAME} 的 hours 不是整数，已忽略该字段。"
            )
            hours = None
        remaining = math.ceil(
            (until - self._now().astimezone(SHANGHAI)).total_seconds()
        )
        result.update(
            {
                "cooldown_until": until.isoformat(),
                "hours": hours,
                "reason": entry.get("reason"),
                "remaining_seconds": max(0, int(remaining)),
                "active": remaining > 0,
            }
        )
        if remaining <= 0:
            result["warnings"].append("风控冷却已过期，已自动失效。")
        return result


class SkippedEventLog:
    """Rolling 7-day counts of non-failure ``skipped`` results."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(data_dir) / SKIPPED_EVENTS_FILENAME
        self._now = now or (lambda: datetime.now(SHANGHAI))

    def record(self, reason: str) -> None:
        reason = str(reason or "").strip()
        if not reason:
            return
        events = self._read()
        events.append(
            {
                "reason": reason,
                "at": self._now().astimezone(SHANGHAI).replace(microsecond=0).isoformat(),
            }
        )
        events = self._prune(events)
        payload = {"version": SKIPPED_EVENTS_VERSION, "events": events}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.path)

    def counts_last_7d(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self._prune(self._read()):
            reason = str(event.get("reason") or "")
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
        return {reason: counts[reason] for reason in sorted(counts)}

    def _read(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return []
        return [event for event in events if isinstance(event, dict)]

    def _prune(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = (
            self._now().astimezone(SHANGHAI) - timedelta(days=SKIPPED_WINDOW_DAYS)
        )
        kept: list[dict[str, Any]] = []
        for event in events:
            moment = _parse_iso(event.get("at"))
            if moment is not None and moment >= cutoff:
                kept.append(event)
        return kept
