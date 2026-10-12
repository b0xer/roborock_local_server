import asyncio
import stat

import pytest
from roborock.data import UserData
from roborock.data.v1.v1_containers import Consumable, StatusV2
from roborock.devices.device_manager import UserParams

from app.auth import CredentialStore
from app.config import Settings
from app.robot import (
    BadRequestError,
    RobotService,
    _code_for,
    consumables_to_list,
    status_to_dict,
)
from roborock.data.v1.v1_clean_modes import VacuumModes

USER_DATA = {
    "uid": 1, "token": "tok", "rruid": "rr1", "region": "eu", "country": "DE", "countrycode": "49",
    "rriot": {"u": "user", "s": "secret", "h": "hkey", "k": "key",
              "r": {"r": "EU", "a": "https://api-eu.example", "m": "ssl://mqtt-eu.example:8883", "l": "https://wood-eu.example"}},
}


def test_credentials_roundtrip(tmp_path):
    store = CredentialStore(tmp_path / "credentials.json")
    params = UserParams(username="user@example.com", user_data=UserData.from_dict(USER_DATA), base_url="https://eu.example")
    store.save(params)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    loaded = store.load()
    assert loaded == params
    store.delete()
    assert store.load() is None


def test_status_to_dict():
    status = StatusV2.from_dict({"state": 8, "battery": 87, "error_code": 0, "clean_area": 12_500_000,
                                 "clean_time": 900, "in_cleaning": 0, "dock_error_status": 0})
    d = status_to_dict(status)
    assert d["state"] == "charging"
    assert d["battery"] == 87
    assert d["error"] is None and d["error_code"] == 0
    assert d["clean_area_m2"] == 12.5
    assert d["fan_speed"] is None  # needs device features, must not raise


def test_consumables_to_list():
    items = {i["key"]: i for i in consumables_to_list(Consumable(main_brush_work_time=540_000, strainer_work_times=30))}
    assert items["main_brush"]["remaining_percent"] == 50
    assert items["main_brush"]["remaining_hours"] == 150.0
    assert items["main_brush"]["resettable"] is True
    assert items["strainer"]["unit"] == "count"
    assert "filter" not in items


def test_code_for():
    options = [VacuumModes.QUIET, VacuumModes.BALANCED, VacuumModes.MAX]
    assert _code_for("Balanced", options, "fan speed") == 102
    with pytest.raises(BadRequestError, match="quiet, balanced, max"):
        _code_for("turbo", options, "fan speed")


def test_room_resolution(tmp_path):
    service = RobotService(Settings(data_dir=tmp_path, _env_file=None))
    service._rooms = [{"segment_id": 16, "name": "Kitchen"}, {"segment_id": 17, "name": "Living Room"}]
    assert asyncio.run(service.resolve_room_ids(["living room", "16"])) == [17, 16]
    with pytest.raises(BadRequestError, match="Available rooms: Kitchen"):
        asyncio.run(service.resolve_room_ids(["Garage"]))


def test_logged_out_without_credentials(tmp_path):
    service = RobotService(Settings(data_dir=tmp_path, _env_file=None))
    asyncio.run(service.start())
    assert service.health()["state"] == "logged_out"
