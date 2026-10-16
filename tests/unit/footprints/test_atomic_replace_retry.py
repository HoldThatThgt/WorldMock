"""原子写在 Windows 上遇到拒绝访问按固定间隔重试（issue #7），其他平台与其他错误立即失败。

只替换 assets 模块里的 os 与 time 引用（tempfile、pathlib 仍用真实 os），在真实临时目录上断言可观察行为。
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import pytest

import worldmock.footprints.assets as assets_module
from worldmock.footprints.assets import _REPLACE_RETRY_SECONDS, atomic_write


class _ReplaceOs:
    """assets 模块专用 os 代理：报告给定平台名，前若干次 replace 抛出给定错误，之后交给真实 os。"""

    def __init__(self, name: str, failures: int, error: OSError):
        self.name, self.failures, self.error = name, failures, error
        self.replaces = 0

    def __getattr__(self, attribute: str):
        """其余属性转发给真实 os。"""
        return getattr(os, attribute)

    def replace(self, source, target) -> None:
        """记录一次替换；还有失败次数时抛出错误。"""
        self.replaces += 1
        if self.replaces <= self.failures:
            raise self.error
        os.replace(source, target)


def _patch(monkeypatch, name: str, failures: int, error: OSError):
    """装上 os 代理与记录等待的 time，返回代理与等待记录。"""
    proxy, sleeps = _ReplaceOs(name, failures, error), []
    monkeypatch.setattr(assets_module, "os", proxy)
    monkeypatch.setattr(assets_module, "time", SimpleNamespace(sleep=sleeps.append))
    return proxy, sleeps


def test_windows_permission_error_is_retried_then_published(tmp_path, monkeypatch, caplog):
    """Windows 上前三次替换被拒绝：按 0.05、0.1、0.2 秒重试后发布新字节，每次重试一条警告。"""
    path = tmp_path / "status.json"
    path.write_bytes(b"old")
    proxy, sleeps = _patch(monkeypatch, "nt", 3, PermissionError("locked"))
    with caplog.at_level(logging.WARNING):
        atomic_write(path, b"new")
    assert path.read_bytes() == b"new"
    assert sleeps == [0.05, 0.1, 0.2] and proxy.replaces == 4
    assert caplog.text.count("Atomic replace denied, retrying in") == 3


def test_windows_permission_error_gives_up_after_bounded_waits(tmp_path, monkeypatch, caplog):
    """一直被拒绝：九次替换、八次等待后抛出 PermissionError 并写原有的错误日志，不留临时文件。"""
    path = tmp_path / "status.json"
    proxy, sleeps = _patch(monkeypatch, "nt", 100, PermissionError("locked"))
    with caplog.at_level(logging.WARNING), pytest.raises(PermissionError):
        atomic_write(path, b"new")
    assert proxy.replaces == 9
    assert sleeps == list(_REPLACE_RETRY_SECONDS) and sum(sleeps) == pytest.approx(12.75)
    assert "Atomic artifact publication failed: status.json" in caplog.text
    assert not path.exists() and not list(tmp_path.glob(".pending-*"))


def test_permission_error_off_windows_is_raised_at_once(tmp_path, monkeypatch):
    """其他平台的拒绝访问立即抛出，不等待。"""
    proxy, sleeps = _patch(monkeypatch, "posix", 1, PermissionError("denied"))
    with pytest.raises(PermissionError):
        atomic_write(tmp_path / "status.json", b"new")
    assert proxy.replaces == 1 and sleeps == []


def test_other_os_errors_are_not_retried(tmp_path, monkeypatch):
    """Windows 上不是拒绝访问的系统错误不重试。"""
    proxy, sleeps = _patch(monkeypatch, "nt", 1, OSError("disk"))
    with pytest.raises(OSError, match="disk"):
        atomic_write(tmp_path / "status.json", b"new")
    assert proxy.replaces == 1 and sleeps == []
