"""Pytest fixtures for the dual-mode ``test_go2rtc.py`` file.

``test_go2rtc.py`` is a script: ``python test_go2rtc.py`` runs the full
integration lifecycle in order (manager launch -> register -> multiplex ->
teardown) and passes the live manager between steps as an argument. Under
pytest those step functions are collected as tests that request parameters,
which is not how they are meant to run.

These fixtures satisfy those parameters with a skip that points at the
supported runner, so a pytest pass does not report phantom errors. The
integration path itself is fully exercised by invoking the script directly.
"""

from __future__ import annotations

import pytest


def _skip_sequential_step() -> None:
    pytest.skip(
        "go2rtc integration runs as an ordered script: `python test_go2rtc.py` "
        "(sequential steps pass a live manager between them and cannot run as "
        "independent pytest cases)"
    )


@pytest.fixture
def binary():
    _skip_sequential_step()


@pytest.fixture
def mgr():
    _skip_sequential_step()
