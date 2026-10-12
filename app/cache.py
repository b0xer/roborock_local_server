"""Crash-safe file cache for python-roborock device data.

Works around two problems with the library's own `FileCache` (python-roborock 7.12.3):
- `DeviceFeaturesTrait.refresh()` stores the live trait object in the cache. It holds an
  RPC channel closure, so pickling fails, and `store_value` has already truncated the file
  by then. An empty cache file then breaks every later start.
- Writes are not atomic, so a crash mid-write can also leave a broken file behind.
"""

import asyncio
import dataclasses
import logging
import pickle
from pathlib import Path

from roborock.device_features import DeviceFeatures
from roborock.devices.cache import CacheData, DeviceCacheData

_LOGGER = logging.getLogger(__name__)


def _plain_features(features: DeviceFeatures | None) -> DeviceFeatures | None:
    if features is None or type(features) is DeviceFeatures:
        return features
    return DeviceFeatures(**{f.name: getattr(features, f.name) for f in dataclasses.fields(DeviceFeatures)})


def _picklable(data: CacheData) -> CacheData:
    """Shallow copy of the cache with live trait objects replaced by plain data."""
    device_info = {
        duid: dataclasses.replace(entry, device_features=_plain_features(entry.device_features))
        for duid, entry in data.device_info.items()
        if isinstance(entry, DeviceCacheData)
    }
    return dataclasses.replace(data, device_info=device_info, device_features=_plain_features(data.device_features))


class SafeFileCache:
    """Implements the python-roborock `Cache` protocol, plus `flush()` to persist to disk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: CacheData | None = None

    async def get(self) -> CacheData:
        if self._data is None:
            self._data = await asyncio.to_thread(self._load)
        return self._data

    async def set(self, value: CacheData) -> None:
        self._data = value

    async def flush(self) -> None:
        if self._data is None:
            return
        payload = pickle.dumps(_picklable(self._data))
        await asyncio.to_thread(self._write, payload)

    def _load(self) -> CacheData:
        try:
            data = pickle.loads(self._path.read_bytes())
            if isinstance(data, CacheData):
                return data
            _LOGGER.warning("Ignoring device cache with unexpected content")
        except FileNotFoundError:
            pass
        except Exception as err:  # noqa: BLE001 - any unreadable cache is just discarded
            _LOGGER.warning("Ignoring unreadable device cache (%s); it will be rebuilt", err)
        return CacheData()

    def _write(self, payload: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        tmp.replace(self._path)
