"""API keys (decided 2026-11-11, with the desktop app): from an environment variable, or
from the system keyring (KWallet or GNOME Keyring through the Secret Service, the macOS
Keychain, Windows' Credential Manager), where Settings saves them for people who don't use
a terminal. Keys are never stored in velocity's own files, and never sent back to the
page: it only learns where a key comes from. An environment variable wins.

Keys: "anthropic" (ANTHROPIC_API_KEY, the API engine), and one per free-engine server,
"openai:<host>" (a hosted free tier's key; its variable is named in the [openai] settings).
"""

import os
from dataclasses import dataclass

SERVICE = "velocity"
ANTHROPIC = "anthropic"
ANTHROPIC_ENV = "ANTHROPIC_API_KEY"


def free_key_name(base_url: str | None) -> str | None:
    """The free engine's key is saved per server, so a key is only ever sent to the
    server it was saved for: "openai:openrouter.ai"."""
    from urllib.parse import urlparse

    host = (urlparse(base_url or "").hostname or "").lower()
    return f"openai:{host}" if host else None


def _known(name: str | None) -> bool:
    return name == ANTHROPIC or bool(
        name and name.startswith("openai:") and len(name) > 7
    )


class KeyStoreError(Exception):
    """A key couldn't be saved or removed (no keyring, or the keyring refused)."""


def keyring_available() -> bool:
    """Whether this computer has a system keyring velocity can use."""
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - any failure means no usable keyring
        return False
    return not isinstance(backend, fail.Keyring) and getattr(backend, "priority", 1) > 0


def from_keyring(name: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, name) or None
    except Exception:  # noqa: BLE001 - a locked or missing keyring: no key
        return None


def get_key(name: str | None, env_var: str | None) -> str | None:
    """The key: the environment variable if it is set, else the keyring's."""
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    return from_keyring(name) if name else None


def anthropic_key() -> str | None:
    return get_key(ANTHROPIC, ANTHROPIC_ENV)


def save_key(name: str, key: str) -> None:
    if not _known(name):
        raise KeyStoreError(f"Unknown key {name!r}.")
    key = key.strip()
    if not key:
        raise KeyStoreError("Paste the key first.")
    if not keyring_available():
        raise KeyStoreError(NO_KEYRING)
    try:
        import keyring

        keyring.set_password(SERVICE, name, key)
    except Exception as e:  # noqa: BLE001 - e.g. the learner declined to unlock it
        raise KeyStoreError(f"The system keyring didn't save the key: {e}") from None


def remove_key(name: str) -> None:
    if not _known(name):
        raise KeyStoreError(f"Unknown key {name!r}.")
    try:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass  # nothing saved
    except Exception as e:  # noqa: BLE001
        raise KeyStoreError(f"The system keyring didn't remove the key: {e}") from None


NO_KEYRING = (
    "This computer has no system keyring velocity can use, so the key can't be saved. "
    "Set it in an environment variable instead, in the terminal that starts velocity."
)


@dataclass(frozen=True)
class KeyStatus:
    source: str | None  # "env", "keyring", or None (no key)
    env_var: str | None  # the variable that would win
    saved: bool  # a key is in the keyring (even if the variable wins)

    def as_json(self) -> dict:
        return {"source": self.source, "env_var": self.env_var, "saved": self.saved}


def status(name: str | None, env_var: str | None) -> KeyStatus:
    """Where the key comes from; never the key itself."""
    saved = bool(name) and from_keyring(name) is not None
    if env_var and os.environ.get(env_var):
        return KeyStatus("env", env_var, saved)
    return KeyStatus("keyring" if saved else None, env_var, saved)
