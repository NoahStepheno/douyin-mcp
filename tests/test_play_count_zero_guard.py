"""播放量「0」的合理性守卫（2026-11-20）。

平台在详情页把被隐藏的播放量渲染成 `0`，卡片上是 `-`。有互动时 `0 播放`
不可能成立，必须记缺失而不是 0——否则会污染任何比率计算（A 老作品连续两天踩）。
"""
from douyin_creator_mcp.browser.extractors import drop_implausible_zero_play


def test_zero_play_with_engagement_becomes_missing():
    metrics = {"play_count": 0, "like_count": 143, "comment_count": 51}
    out = drop_implausible_zero_play(dict(metrics))
    assert out["play_count"] is None


def test_zero_play_with_completion_rate_becomes_missing():
    metrics = {"play_count": 0, "completion_rate": 0.0218}
    assert drop_implausible_zero_play(dict(metrics))["play_count"] is None


def test_follower_gain_alone_is_enough():
    metrics = {"play_count": 0, "follower_gain": 3}
    assert drop_implausible_zero_play(dict(metrics))["play_count"] is None


def test_genuine_zero_play_is_kept():
    metrics = {"play_count": 0, "like_count": 0, "comment_count": 0, "completion_rate": None}
    assert drop_implausible_zero_play(dict(metrics))["play_count"] == 0


def test_nonzero_play_untouched():
    metrics = {"play_count": 636, "like_count": 10}
    assert drop_implausible_zero_play(dict(metrics))["play_count"] == 636


def test_missing_play_untouched():
    metrics = {"play_count": None, "like_count": 10}
    assert drop_implausible_zero_play(dict(metrics))["play_count"] is None
