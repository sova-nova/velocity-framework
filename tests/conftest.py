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


class MemoryKeyring:
    """A keyring that lives only in this test (added 2026-11-11 with keys in the keyring),
    so no test reads or writes the learner's real one, and none depends on the machine."""

    priority = 1

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if self.passwords.pop((service, username), None) is None:
            raise PasswordDeleteError("not saved")


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend

    class Backend(MemoryKeyring, KeyringBackend):
        pass

    backend = Backend()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    yield backend
    keyring.set_keyring(previous)
