"""RobotService: owns the python-roborock connection and exposes robot operations.

REST, MCP and the web UI all go through this class, so behaviour is identical everywhere.
"""

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeVar

from roborock import RoborockCommand
from roborock.const import (
    CLEANING_BRUSH_REPLACE_TIME,
    DUST_COLLECTION_REPLACE_TIME,
    FILTER_REPLACE_TIME,
    MAIN_BRUSH_REPLACE_TIME,
    MOP_ROLLER_REPLACE_TIME,
    SENSOR_DIRTY_REPLACE_TIME,
    SIDE_BRUSH_REPLACE_TIME,
    STRAINER_REPLACE_TIME,
)
from roborock.devices.device import RoborockDevice
from roborock.devices.device_manager import DeviceManager, UserParams, create_device_manager
from roborock.devices.traits.v1 import PropertiesApi
from roborock.devices.traits.v1.consumeable import ConsumableAttribute
from roborock.exceptions import (
    RoborockException,
    RoborockInvalidCredentials,
    RoborockTimeout,
    RoborockUnsupportedFeature,
)

from .auth import CredentialStore
from .cache import SafeFileCache
from .config import Settings

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class RobotState(enum.StrEnum):
    LOGGED_OUT = "logged_out"
    CONNECTING = "connecting"
    READY = "ready"
    REAUTH_REQUIRED = "reauth_required"
    ERROR = "error"


class RobotError(Exception):
    status_code = 500


class NotReadyError(RobotError):
    status_code = 503


class BadRequestError(RobotError):
    status_code = 400


class DeviceCommandError(RobotError):
    status_code = 502


# key, label, Consumable field, replacement interval, unit ("s" = seconds of use, "count" = cycles)
_CONSUMABLES: list[tuple[str, str, str, int, str]] = [
    ("main_brush", "Main brush", "main_brush_work_time", MAIN_BRUSH_REPLACE_TIME, "s"),
    ("side_brush", "Side brush", "side_brush_work_time", SIDE_BRUSH_REPLACE_TIME, "s"),
    ("filter", "Filter", "filter_work_time", FILTER_REPLACE_TIME, "s"),
    ("sensors", "Sensors", "sensor_dirty_time", SENSOR_DIRTY_REPLACE_TIME, "s"),
    ("mop_roller", "Mop roller", "moproller_work_time", MOP_ROLLER_REPLACE_TIME, "s"),
    ("strainer", "Dock strainer", "strainer_work_times", STRAINER_REPLACE_TIME, "count"),
    ("cleaning_brush", "Dock cleaning brush", "cleaning_brush_work_times", CLEANING_BRUSH_REPLACE_TIME, "count"),
    ("dust_bag", "Dust bag", "dust_collection_work_times", DUST_COLLECTION_REPLACE_TIME, "count"),
]
_RESETTABLE_FIELDS = {attr.value for attr in ConsumableAttribute}


def _enum_name(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return getattr(value, "display_name", None) or value.name
    return value


def _safe(fn: Callable[[], T]) -> T | None:
    """Derived trait properties can raise on partial data; never let that break a response."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def status_to_dict(s: Any) -> dict[str, Any]:
    error = s.error_code if s.error_code not in (None, 0) else None
    dock_error = s.dock_error_status if s.dock_error_status not in (None, 0) else None
    return {
        "state": s.state_name,
        "state_code": int(s.state) if s.state is not None else None,
        "battery": s.battery,
        "charging": _safe(lambda: s.is_battery_charging),
        "in_cleaning": bool(s.in_cleaning) if s.in_cleaning is not None else None,
        "in_returning": bool(s.in_returning) if s.in_returning is not None else None,
        "error": _enum_name(error),
        "error_code": int(error) if error is not None else 0,
        "dock_error": _enum_name(dock_error),
        "clean_time_s": s.clean_time,
        "clean_area_m2": s.square_meter_clean_area,
        "clean_percent": s.clean_percent,
        "cleaning_mode": _safe(lambda: s.current_cleaning_mode_name),
        "fan_speed": _safe(lambda: s.fan_speed_name),
        "water_level": _safe(lambda: s.water_mode_name),
        "mop_route": _safe(lambda: s.mop_route_name),
        "dock_type": _enum_name(s.dock_type),
        "dust_collection_status": s.dust_collection_status,
        "wash_status": s.wash_status,
        "dry_status": s.dry_status,
        "water_shortage": bool(s.water_shortage_status) if s.water_shortage_status is not None else None,
    }


def consumables_to_list(c: Any) -> list[dict[str, Any]]:
    items = []
    for key, label, field, total, unit in _CONSUMABLES:
        used = getattr(c, field, None)
        if used is None:
            continue
        remaining = max(total - used, 0)
        item: dict[str, Any] = {
            "key": key,
            "name": label,
            "unit": unit,
            "used": used,
            "lifetime": total,
            "remaining_percent": round(100 * remaining / total),
            "resettable": field in _RESETTABLE_FIELDS,
        }
        if unit == "s":
            item["remaining_hours"] = round(remaining / 3600, 1)
        items.append(item)
    return items


def _code_for(name: str, options: Iterable[Any], what: str) -> int:
    wanted = name.strip().lower()
    options = list(options)
    for option in options:
        if wanted in (str(option.value).lower(), option.display_name.lower()):
            return option.code
    choices = ", ".join(_dedupe(o.display_name for o in options))
    raise BadRequestError(f"Unsupported {what} {name!r}. Choose one of: {choices}")


class RobotService:
    def __init__(self, settings: Settings, store: CredentialStore | None = None) -> None:
        self._settings = settings
        self._store = store or CredentialStore(settings.credentials_path)
        self._cache = SafeFileCache(settings.cache_path)
        self._manager: DeviceManager | None = None
        self._device: RoborockDevice | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._command_lock = asyncio.Lock()
        self._state = RobotState.LOGGED_OUT
        self._last_error: str | None = None
        self._cloud_unauthorized = False
        self._status_at = 0.0
        self._rooms: list[dict[str, Any]] = []

    # ----------------------------------------------------------------- lifecycle

    @property
    def state(self) -> RobotState:
        return self._state

    async def start(self) -> None:
        try:
            params = self._store.load()
        except Exception as err:  # noqa: BLE001
            self._set_error(f"Could not read stored credentials: {err}")
            return
        if params is None:
            self._state = RobotState.LOGGED_OUT
            return
        self._connect_task = asyncio.create_task(self._connect_loop(params))

    async def stop(self) -> None:
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            self._connect_task = None
        if self._manager:
            await self._manager.close()
            self._manager = None
        self._device = None
        await self._flush_cache()

    async def login(self, params: UserParams) -> None:
        """Store fresh credentials and reconnect with them."""
        await self.stop()
        self._store.save(params)
        self._reset_cache()
        self._cloud_unauthorized = False
        await self.start()

    async def logout(self) -> None:
        await self.stop()
        self._store.delete()
        self._reset_cache()
        self._state = RobotState.LOGGED_OUT
        self._last_error = None

    def _reset_cache(self) -> None:
        self._settings.cache_path.unlink(missing_ok=True)
        self._cache = SafeFileCache(self._settings.cache_path)

    async def _flush_cache(self) -> None:
        try:
            await self._cache.flush()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not write device cache: %s", err)

    def _set_error(self, message: str, state: RobotState = RobotState.ERROR) -> None:
        _LOGGER.error(message)
        self._state = state
        self._last_error = message

    def _on_unauthorized(self) -> None:
        _LOGGER.warning("Roborock cloud rejected the stored credentials (revoked token or rate limit)")
        self._cloud_unauthorized = True

    def _on_device_ready(self, device: RoborockDevice) -> None:
        _LOGGER.info("Connected to %s (local=%s)", device.name, device.is_local_connected)
        # Persist the network info learned during connect so restarts work without the cloud.
        asyncio.get_running_loop().create_task(self._flush_cache())

    async def _connect_loop(self, params: UserParams) -> None:
        delay = 10.0
        while True:
            self._state = RobotState.CONNECTING
            try:
                manager = await create_device_manager(
                    params,
                    cache=self._cache,
                    mqtt_session_unauthorized_hook=self._on_unauthorized,
                )
            except RoborockInvalidCredentials as err:
                self._set_error(f"Stored credentials were rejected, please log in again: {err}",
                                RobotState.REAUTH_REQUIRED)
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - network errors come in many shapes
                self._last_error = f"Could not reach Roborock services: {err}"
                _LOGGER.warning("%s (retrying in %.0fs)", self._last_error, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)
                continue

            devices = await manager.get_devices()
            device = self._select_device(devices)
            if device is None:
                await manager.close()
                names = ", ".join(f"{d.name} ({d.product.model})" for d in devices) or "none"
                self._set_error(f"No matching V1 vacuum found. Devices on account: {names}")
                return
            self._manager = manager
            self._device = device
            device.add_ready_callback(self._on_device_ready)
            self._state = RobotState.READY
            self._last_error = None
            _LOGGER.info("Using device %s (%s)", device.name, device.product.model)
            return

    def _select_device(self, devices: list[RoborockDevice]) -> RoborockDevice | None:
        candidates = [d for d in devices if d.v1_properties is not None]
        wanted = self._settings.roborock_device.strip().lower()
        if not wanted:
            return candidates[0] if candidates else None
        for d in candidates:
            if wanted in (d.duid.lower(), d.name.lower()):
                return d
        return None

    # ------------------------------------------------------------------ helpers

    def _props(self) -> PropertiesApi:
        if self._device is None:
            detail = f": {self._last_error}" if self._last_error else ""
            raise NotReadyError(f"Robot is not available (state: {self._state}){detail}")
        if not self._device.is_connected:
            raise NotReadyError("Robot is not connected yet; reconnecting in the background")
        assert self._device.v1_properties is not None
        return self._device.v1_properties

    async def _run(self, fn: Callable[[], Awaitable[T]]) -> T:
        try:
            return await fn()
        except RobotError:
            raise
        except RoborockUnsupportedFeature as err:
            raise BadRequestError(str(err)) from err
        except RoborockTimeout as err:
            raise DeviceCommandError(f"Robot did not respond: {err}") from err
        except RoborockException as err:
            raise DeviceCommandError(str(err) or type(err).__name__) from err

    async def _command(self, command: RoborockCommand | str, params: Any = None) -> dict[str, Any]:
        props = self._props()
        async with self._command_lock:
            result = await self._run(lambda: props.command.send(command, params=params))
        self._status_at = 0.0
        return {"ok": True, "command": str(command), "result": result}

    # -------------------------------------------------------------------- reads

    def health(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "error": self._last_error,
            "device_connected": bool(self._device and self._device.is_connected),
            "local_connection": bool(self._device and self._device.is_local_connected),
            "cloud_unauthorized": self._cloud_unauthorized,
            "token_required": bool(self._settings.api_token),
        }

    async def devices(self) -> dict[str, Any]:
        devices = await self._manager.get_devices() if self._manager else []
        return {
            "account": self._store.load().username if self._store.exists() else None,
            "selected": self._device.duid if self._device else None,
            "devices": [
                {
                    "duid": d.duid,
                    "name": d.name,
                    "model": d.product.model,
                    "firmware": d.device_info.fv,
                    "supported": d.v1_properties is not None,
                    "connected": d.is_connected,
                    "local_connection": d.is_local_connected,
                }
                for d in devices
            ],
        }

    async def status(self, force: bool = False) -> dict[str, Any]:
        props = self._props()
        if force or time.monotonic() - self._status_at > self._settings.status_cache_seconds:
            await self._run(props.status.refresh)
            self._status_at = time.monotonic()
        return status_to_dict(props.status)

    async def rooms(self) -> list[dict[str, Any]]:
        props = self._props()
        await self._run(props.rooms.refresh)
        self._rooms = [{"segment_id": r.segment_id, "name": r.name} for r in props.rooms.rooms or []]
        return self._rooms

    async def consumables(self) -> list[dict[str, Any]]:
        props = self._props()
        await self._run(props.consumables.refresh)
        return consumables_to_list(props.consumables)

    async def clean_summary(self) -> dict[str, Any]:
        props = self._props()
        await self._run(props.clean_summary.refresh)
        s = props.clean_summary
        last = s.last_clean_record
        return {
            "total_clean_time_h": round(s.clean_time / 3600, 1) if s.clean_time is not None else None,
            "total_clean_area_m2": s.square_meter_clean_area,
            "clean_count": s.clean_count,
            "dust_collection_count": s.dust_collection_count,
            "last_clean": None if last is None else {
                "start": last.begin_datetime,
                "end": last.end_datetime,
                "duration_s": last.duration,
                "area_m2": last.square_meter_area,
                "complete": bool(last.complete) if last.complete is not None else None,
                "finish_reason": _enum_name(last.finish_reason),
            },
        }

    async def options(self) -> dict[str, list[str]]:
        s = self._props().status
        return {
            "cleaning_modes": [m.value for m in _safe(lambda: s.cleaning_mode_options) or []],
            "fan_speeds": _dedupe(m.display_name for m in _safe(lambda: s.fan_speed_options) or []),
            "water_levels": _dedupe(m.display_name for m in _safe(lambda: s.water_mode_options) or []),
            "mop_routes": _dedupe(m.display_name for m in _safe(lambda: s.mop_route_options) or []),
        }

    async def map_png(self) -> bytes:
        props = self._props()
        await self._run(props.map_content.refresh)
        if not props.map_content.image_content:
            raise DeviceCommandError("The robot did not return a map")
        return props.map_content.image_content

    # ----------------------------------------------------------------- commands

    async def start_cleaning(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.APP_START)

    async def pause(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.APP_PAUSE)

    async def stop_cleaning(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.APP_STOP)

    async def dock(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.APP_CHARGE)

    async def locate(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.FIND_ME)

    async def empty_dustbin(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.APP_START_COLLECT_DUST)

    async def wash_mop(self) -> dict[str, Any]:
        return await self._command(RoborockCommand.APP_START_WASH)

    async def resolve_room_ids(self, rooms: Iterable[str | int]) -> list[int]:
        known = self._rooms or await self.rooms()
        by_id = {r["segment_id"]: r for r in known}
        by_name = {r["name"].strip().lower(): r for r in known}
        ids = []
        for item in rooms:
            text = str(item).strip()
            if text.isdigit() and int(text) in by_id:
                ids.append(int(text))
            elif text.lower() in by_name:
                ids.append(by_name[text.lower()]["segment_id"])
            else:
                available = ", ".join(f"{r['name']} ({r['segment_id']})" for r in known) or "none"
                raise BadRequestError(f"Unknown room {text!r}. Available rooms: {available}")
        if not ids:
            raise BadRequestError("No rooms given")
        return ids

    async def clean_rooms(self, rooms: Iterable[str | int], repeat: int = 1) -> dict[str, Any]:
        if not 1 <= repeat <= 3:
            raise BadRequestError("repeat must be between 1 and 3")
        ids = await self.resolve_room_ids(rooms)
        result = await self._command(RoborockCommand.APP_SEGMENT_CLEAN, [{"segments": ids, "repeat": repeat}])
        result["segments"] = ids
        return result

    async def set_settings(
        self,
        cleaning_mode: str | None = None,
        fan_speed: str | None = None,
        water_level: str | None = None,
        mop_route: str | None = None,
    ) -> dict[str, Any]:
        props = self._props()
        s = props.status
        applied: dict[str, str] = {}
        if cleaning_mode:
            async with self._command_lock:
                await self._run(lambda: s.set_cleaning_mode(cleaning_mode.strip().lower()))
            applied["cleaning_mode"] = cleaning_mode
        if fan_speed:
            await self._command(RoborockCommand.SET_CUSTOM_MODE, [_code_for(fan_speed, s.fan_speed_options, "fan speed")])
            applied["fan_speed"] = fan_speed
        if water_level:
            code = _code_for(water_level, s.water_mode_options, "water level")
            await self._command(RoborockCommand.SET_WATER_BOX_CUSTOM_MODE, [code])
            applied["water_level"] = water_level
        if mop_route:
            await self._command(RoborockCommand.SET_MOP_MODE, [_code_for(mop_route, s.mop_route_options, "mop route")])
            applied["mop_route"] = mop_route
        if not applied:
            raise BadRequestError("Nothing to change")
        self._status_at = 0.0
        return {"ok": True, "applied": applied}

    async def reset_consumable(self, key: str) -> dict[str, Any]:
        props = self._props()
        field = next((f for k, _, f, _, _ in _CONSUMABLES if key in (k, f)), None)
        if field is None or field not in _RESETTABLE_FIELDS:
            resettable = ", ".join(k for k, _, f, _, _ in _CONSUMABLES if f in _RESETTABLE_FIELDS)
            raise BadRequestError(f"Consumable {key!r} cannot be reset. Resettable: {resettable}")
        async with self._command_lock:
            await self._run(lambda: props.consumables.reset_consumable(ConsumableAttribute.from_str(field)))
        return {"ok": True, "reset": key}

    async def raw_command(self, method: str, params: Any = None) -> dict[str, Any]:
        if not self._settings.allow_raw_commands:
            raise BadRequestError("Raw commands are disabled (set ALLOW_RAW_COMMANDS=true)")
        return await self._command(method, params)
