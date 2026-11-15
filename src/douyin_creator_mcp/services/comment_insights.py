"""Comment intent classification and insight aggregation (v1).

Categories are keyword based and intentionally **multi-label**: one comment may
hit several categories. Rules live in ``DATA_DIR/comment_keywords.json`` and
fall back to the built-in list from the ticket.

Rule file format::

    {
      "version": "comment-keywords-v1",
      "categories": {"求链接": ["链接", "在哪买"], "提问": ["怎么"]}
    }

An unreadable/invalid file never breaks the tool: the built-in rules and their
version are used and a warning is returned to the caller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

COMMENT_KEYWORD_RULE_VERSION = "comment-keywords-v1"
COMMENT_KEYWORDS_FILENAME = "comment_keywords.json"
TOP_WORDS_LIMIT = 10

DEFAULT_COMMENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "求链接": ("链接", "在哪买", "怎么买", "求个", "发我", "私我", "入口", "地址"),
    "提问": (
        "怎么",
        "如何",
        "能不能",
        "可不可以",
        "为什么",
        "求教程",
        "有没有",
        "多少钱",
        "收费",
        "价格",
    ),
    "负反馈": ("难用", "没用", "听不懂", "假的", "水", "广告"),
    "正反馈": ("学到了", "收藏了", "太有用", "谢谢", "牛"),
}


def load_keyword_rules(
    data_dir: Path | str | None,
) -> tuple[dict[str, tuple[str, ...]], str, list[str]]:
    """Load keyword rules, returning ``(rules, version, warnings)``."""

    if data_dir is None:
        return _builtin_rules(), COMMENT_KEYWORD_RULE_VERSION, []
    path = Path(data_dir) / COMMENT_KEYWORDS_FILENAME
    if not path.is_file():
        return _builtin_rules(), COMMENT_KEYWORD_RULE_VERSION, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return (
            _builtin_rules(),
            COMMENT_KEYWORD_RULE_VERSION,
            [f"{COMMENT_KEYWORDS_FILENAME} 无法读取，已使用内置词表。"],
        )
    rules = _rules_from_payload(payload)
    if rules is None:
        return (
            _builtin_rules(),
            COMMENT_KEYWORD_RULE_VERSION,
            [f"{COMMENT_KEYWORDS_FILENAME} 格式无效，已使用内置词表。"],
        )
    version = (
        str(payload.get("version")).strip()
        if isinstance(payload.get("version"), str)
        else COMMENT_KEYWORD_RULE_VERSION
    )
    return rules, version or COMMENT_KEYWORD_RULE_VERSION, []


def _builtin_rules() -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(words) for name, words in DEFAULT_COMMENT_KEYWORDS.items()
    }


def _rules_from_payload(payload: Any) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(payload, dict):
        return None
    categories = payload.get("categories")
    if not isinstance(categories, dict) or not categories:
        return None
    rules: dict[str, tuple[str, ...]] = {}
    for name, words in categories.items():
        label = str(name).strip()
        if not label or not isinstance(words, list):
            return None
        cleaned = tuple(
            word.strip()
            for word in words
            if isinstance(word, str) and word.strip()
        )
        if not cleaned:
            return None
        rules[label] = cleaned
    return rules


def classify_comment(
    content: str, rules: dict[str, tuple[str, ...]]
) -> list[str]:
    """Return every category whose keyword appears in ``content`` (multi-label)."""

    text = str(content or "")
    return [
        category
        for category, words in rules.items()
        if any(word in text for word in words)
    ]


def build_insights(
    comments: Iterable[dict[str, Any]],
    rules: dict[str, tuple[str, ...]],
    rule_version: str,
) -> dict[str, Any]:
    """Aggregate returned comments, excluding the creator's own replies.

    ``insights`` always describes the returned (already keyword-filtered)
    comment set, so consumers can read counts next to ``comments``.
    """

    materialized = [comment for comment in comments if isinstance(comment, dict)]
    excluded = sum(1 for comment in materialized if comment.get("is_author_reply"))
    analysed = [comment for comment in materialized if not comment.get("is_author_reply")]
    categories = {category: 0 for category in rules}
    word_counts: dict[str, int] = {}
    for comment in analysed:
        content = str(comment.get("content") or "")
        for category in classify_comment(content, rules):
            categories[category] += 1
        for words in rules.values():
            for word in words:
                hits = content.count(word)
                if hits:
                    word_counts[word] = word_counts.get(word, 0) + hits
    top_words = [
        {"word": word, "count": count}
        for word, count in sorted(
            word_counts.items(), key=lambda item: (-item[1], item[0])
        )[:TOP_WORDS_LIMIT]
    ]
    return {
        # ``total_comments`` is the analysed set (author replies excluded), so
        # ``total_comments + excluded_author_replies`` equals the returned list.
        "total_comments": len(analysed),
        "excluded_author_replies": excluded,
        "categories": categories,
        "multi_label": True,
        "keyword_rule_version": rule_version,
        "top_words": top_words,
    }
