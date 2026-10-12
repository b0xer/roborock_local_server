"""Roborock account login (email code flow) and credential persistence.

The Roborock cloud is only needed to obtain the account's `UserData` (which contains
the per-device local keys). Once stored, the robot is controlled over the local network
and the cloud is used only as a fallback.
"""

import json
import logging
import os
import time
from pathlib import Path

from roborock.data import UserData
from roborock.devices.device_manager import UserParams
from roborock.web_api import RoborockApiClient

_LOGGER = logging.getLogger(__name__)

# How long a requested email code is considered pending.
_PENDING_TTL_SECONDS = 15 * 60


class CredentialStore:
    """Stores `UserParams` as JSON (readable only by the owner)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> UserParams | None:
        if not self._path.exists():
            return None
        raw = json.loads(self._path.read_text())
        return UserParams(
            username=raw["username"],
            user_data=UserData.from_dict(raw["user_data"]),
            base_url=raw.get("base_url"),
        )

    def save(self, params: UserParams) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "username": params.username,
            "base_url": params.base_url,
            "user_data": params.user_data.as_dict(),
        }
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self._path)

    def delete(self) -> None:
        self._path.unlink(missing_ok=True)


class LoginFlow:
    """Two-step email code login. Keeps the API client between both steps."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[RoborockApiClient, float]] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if now - v[1] < _PENDING_TTL_SECONDS}

    async def request_code(self, email: str) -> None:
        self._prune()
        client = RoborockApiClient(username=email)
        await client.request_code_v4()
        self._pending[email.lower()] = (client, time.monotonic())
        _LOGGER.info("Login code requested")

    async def complete(self, email: str, code: str) -> UserParams:
        self._prune()
        entry = self._pending.get(email.lower())
        client = entry[0] if entry else RoborockApiClient(username=email)
        user_data = await client.code_login_v4(code.strip())
        base_url = await client.base_url
        self._pending.pop(email.lower(), None)
        return UserParams(username=email, user_data=user_data, base_url=base_url)
