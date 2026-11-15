"""Shared pytest fixtures: no network, no real Douyin data.

Playwright is used only to render de-identified local HTML strings via
``page.set_content``; when no Chromium build is available those tests skip and
the remaining logic tests still run.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from douyin_creator_mcp.config import Settings

SHANGHAI = ZoneInfo("Asia/Shanghai")
FIXED_NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=SHANGHAI)


@pytest.fixture()
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture(scope="session")
def chromium_browser() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - dependency is declared
        pytest.skip("playwright is not installed")
    manager = None
    browser = None
    errors: list[str] = []
    try:
        manager = sync_playwright().start()
        for options in ({"headless": True}, {"headless": True, "channel": "chrome"}):
            try:
                browser = manager.chromium.launch(**options)
                break
            except Exception as exc:  # pragma: no cover - environment dependent
                errors.append(f"{options}: {exc}")
    except Exception as exc:  # pragma: no cover - environment dependent
        errors.append(str(exc))
    if browser is None:
        if manager is not None:
            manager.stop()
        pytest.skip("No local Chromium/Chrome for fixture rendering: " + " | ".join(errors))
    yield browser
    browser.close()
    manager.stop()


@pytest.fixture()
def dom_page(chromium_browser: Any) -> Any:
    page = chromium_browser.new_page()
    yield page
    page.close()
