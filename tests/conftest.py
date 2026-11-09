"""Shared test set-up.

Every test gets its own data and config folders, so no test can read or write the
learner's real ones (`~/.local/share/velocity`, `~/.config/velocity`). Before this, tests
that didn't set XDG_DATA_HOME themselves wrote their temporary PDFs into the real library
index (found 2026-11-09). Fixtures that set these variables themselves still win: they
run after this one.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_folders(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
