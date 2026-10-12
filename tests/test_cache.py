import asyncio
import dataclasses

from roborock.device_features import DeviceFeatures
from roborock.devices.cache import CacheData, DeviceCacheData

from app.cache import SafeFileCache


class LiveFeatures(DeviceFeatures):
    """Mimics DeviceFeaturesTrait: a DeviceFeatures subclass holding an unpicklable closure."""


def make_live_features() -> LiveFeatures:
    plain = DeviceFeatures(**{f.name: False for f in dataclasses.fields(DeviceFeatures)})
    live = LiveFeatures(**dataclasses.asdict(plain))
    live._rpc = lambda: None  # local lambdas cannot be pickled
    return live


def test_empty_cache_file_is_discarded(tmp_path):
    path = tmp_path / "device_cache.pkl"
    path.write_bytes(b"")
    data = asyncio.run(SafeFileCache(path).get())
    assert data == CacheData()


def test_live_trait_objects_are_stored_as_plain_data(tmp_path):
    path = tmp_path / "device_cache.pkl"

    async def write():
        cache = SafeFileCache(path)
        data = await cache.get()
        data.device_info["duid1"] = DeviceCacheData(device_features=make_live_features())
        await cache.flush()
        # The in-memory cache keeps the live object the library put there.
        assert type(data.device_info["duid1"].device_features) is LiveFeatures

    asyncio.run(write())
    reloaded = asyncio.run(SafeFileCache(path).get())
    assert type(reloaded.device_info["duid1"].device_features) is DeviceFeatures
    assert not path.with_suffix(".tmp").exists()
