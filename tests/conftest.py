"""Shared fixtures: an app wired to a fake robot so tests never touch a real device."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.robot import BadRequestError, NotReadyError

FAKE_STATUS = {"state": "charging", "battery": 100, "error": None, "fan_speed": "balanced"}
FAKE_ROOMS = [{"segment_id": 16, "name": "Kitchen"}, {"segment_id": 17, "name": "Living room"}]
# Smallest valid PNG (1x1 transparent pixel).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class FakeRobotService:
    def __init__(self) -> None:
        self.commands: list[tuple[str, Any]] = []
        self.connected = True
        self.token_required = False

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def health(self) -> dict[str, Any]:
        return {"state": "ready", "error": None, "device_connected": self.connected,
                "local_connection": True, "cloud_unauthorized": False, "token_required": self.token_required}

    def _check(self) -> None:
        if not self.connected:
            raise NotReadyError("Robot is not connected yet")

    async def status(self, force: bool = False) -> dict[str, Any]:
        self._check()
        return FAKE_STATUS

    async def rooms(self) -> list[dict[str, Any]]:
        return FAKE_ROOMS

    async def map_png(self) -> bytes:
        return PNG

    async def _cmd(self, name: str, params: Any = None) -> dict[str, Any]:
        self._check()
        self.commands.append((name, params))
        return {"ok": True, "command": name, "result": ["ok"]}

    async def start_cleaning(self):
        return await self._cmd("app_start")

    async def dock(self):
        return await self._cmd("app_charge")

    async def clean_rooms(self, rooms, repeat=1):
        by_name = {r["name"].lower(): r["segment_id"] for r in FAKE_ROOMS}
        ids = []
        for room in rooms:
            if str(room).lower() not in by_name:
                raise BadRequestError(f"Unknown room {room!r}")
            ids.append(by_name[str(room).lower()])
        return await self._cmd("app_segment_clean", [{"segments": ids, "repeat": repeat}])


@pytest.fixture
def fake() -> FakeRobotService:
    return FakeRobotService()


def make_client(fake: FakeRobotService, tmp_path, **settings: Any) -> TestClient:
    fake.token_required = bool(settings.get("api_token"))
    app = create_app(Settings(data_dir=tmp_path, _env_file=None, **settings), service=fake)  # type: ignore[arg-type]
    return TestClient(app)


@pytest.fixture
def client(fake, tmp_path):
    with make_client(fake, tmp_path) as c:
        yield c


@pytest.fixture
def token_client(fake, tmp_path):
    with make_client(fake, tmp_path, api_token="s3cret") as c:
        yield c
