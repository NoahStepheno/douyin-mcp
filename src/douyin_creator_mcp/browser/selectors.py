"""Creator-center DOM selectors, tolerant of Douyin's class renames.

2026-11-21: the creator work list moved from
``[class*="video-card-content-"]`` / ``[class*="info-title-text-"]`` to
``article[class*="creator-work-card"]`` / ``[class*="creator-work-card__title"]``
and the cards stopped exposing ``<a href="...item_id=...">``. Every selector
here matches both layouts so a future rename can be absorbed by editing one
file. Selector lists are injected into page scripts as JSON string literals.
"""

from __future__ import annotations

CARD_SEL = ', '.join(
    (
        '[class*="video-card-content"]',
        'article[class*="creator-work-card"]',
        '[class*="creator-work-card"]',
    )
)

TITLE_SEL = ', '.join(
    (
        '[class*="info-title-text"]',
        '[class*="creator-work-card__title"]',
    )
)

TITLE_ALT_SEL = ', '.join(
    (
        '[class*="info-title-operation"]',
        '[class*="creator-work-card__heading"]',
    )
)

TIME_SEL = ', '.join(
    (
        '[class*="info-time"]',
        '[class*="creator-work-card__publish"]',
    )
)

STATUS_SEL = ', '.join(
    (
        '[class*="info-status"]',
        '[class*="creator-work-card__status"]',
    )
)

METRIC_LABEL_SEL = '[class*="metric-label"]'
METRIC_VALUE_SEL = '[class*="metric-value"]'

COVER_SEL = 'img[src]'

#: Status words the card renders as plain text when no status node exists.
STATUS_WORDS = ('私密', '仅自己可见', '已发布', '发布成功', '审核中', '未通过')

_SELECTORS = {
    "__CARD_SEL__": CARD_SEL,
    "__TITLE_SEL__": TITLE_SEL,
    "__TITLE_ALT_SEL__": TITLE_ALT_SEL,
    "__TIME_SEL__": TIME_SEL,
    "__STATUS_SEL__": STATUS_SEL,
    "__METRIC_LABEL_SEL__": METRIC_LABEL_SEL,
    "__METRIC_VALUE_SEL__": METRIC_VALUE_SEL,
    "__COVER_SEL__": COVER_SEL,
    "__STATUS_WORDS__": list(STATUS_WORDS),
}


def render(script: str) -> str:
    """Replace ``__X_SEL__`` placeholders with JSON-encoded selector literals."""

    import json

    for placeholder, selector in _SELECTORS.items():
        script = script.replace(placeholder, json.dumps(selector))
    return script
