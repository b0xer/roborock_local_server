"""REST API routes."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from roborock.exceptions import RoborockException

from .auth import LoginFlow
from .robot import RobotService


class EmailBody(BaseModel):
    email: str = Field(min_length=3)


class LoginBody(EmailBody):
    code: str = Field(min_length=4)


class CleanRoomsBody(BaseModel):
    rooms: list[str | int] = Field(min_length=1, description="Room names or segment IDs")
    repeat: int = Field(default=1, ge=1, le=3)


class SettingsBody(BaseModel):
    cleaning_mode: str | None = None
    fan_speed: str | None = None
    water_level: str | None = None
    mop_route: str | None = None


class RawCommandBody(BaseModel):
    method: str
    params: Any = None


def build_router(service: RobotService, login_flow: LoginFlow, default_email: str = "") -> APIRouter:
    router = APIRouter(prefix="/api")

    # --- health & auth -----------------------------------------------------

    @router.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        return service.health()

    @router.get("/auth/info", tags=["auth"])
    async def auth_info() -> dict[str, Any]:
        return {"default_email": default_email or None}

    @router.post("/auth/request-code", tags=["auth"])
    async def request_code(body: EmailBody) -> dict[str, Any]:
        try:
            await login_flow.request_code(body.email.strip())
        except RoborockException as err:
            raise HTTPException(400, f"Could not request a code: {err}") from err
        return {"ok": True, "message": "A login code was sent to your email"}

    @router.post("/auth/login", tags=["auth"])
    async def login(body: LoginBody) -> dict[str, Any]:
        try:
            params = await login_flow.complete(body.email.strip(), body.code)
        except RoborockException as err:
            raise HTTPException(400, f"Login failed: {err}") from err
        await service.login(params)
        return {"ok": True}

    @router.post("/auth/logout", tags=["auth"])
    async def logout() -> dict[str, Any]:
        await service.logout()
        return {"ok": True}

    @router.get("/devices", tags=["system"])
    async def devices() -> dict[str, Any]:
        return await service.devices()

    # --- reads ---------------------------------------------------------------

    @router.get("/status", tags=["read"])
    async def status(refresh: bool = False) -> dict[str, Any]:
        return await service.status(force=refresh)

    @router.get("/rooms", tags=["read"])
    async def rooms() -> list[dict[str, Any]]:
        return await service.rooms()

    @router.get("/consumables", tags=["read"])
    async def consumables() -> list[dict[str, Any]]:
        return await service.consumables()

    @router.get("/summary", tags=["read"])
    async def summary() -> dict[str, Any]:
        return await service.clean_summary()

    @router.get("/options", tags=["read"])
    async def options() -> dict[str, list[str]]:
        return await service.options()

    @router.get("/map.png", tags=["read"], response_class=Response,
                responses={200: {"content": {"image/png": {}}}})
    async def map_png() -> Response:
        return Response(await service.map_png(), media_type="image/png", headers={"Cache-Control": "no-store"})

    # --- commands ------------------------------------------------------------

    @router.post("/start", tags=["control"])
    async def start() -> dict[str, Any]:
        return await service.start_cleaning()

    @router.post("/pause", tags=["control"])
    async def pause() -> dict[str, Any]:
        return await service.pause()

    @router.post("/stop", tags=["control"])
    async def stop() -> dict[str, Any]:
        return await service.stop_cleaning()

    @router.post("/dock", tags=["control"])
    async def dock() -> dict[str, Any]:
        return await service.dock()

    @router.post("/locate", tags=["control"])
    async def locate() -> dict[str, Any]:
        return await service.locate()

    @router.post("/clean/rooms", tags=["control"])
    async def clean_rooms(body: CleanRoomsBody) -> dict[str, Any]:
        return await service.clean_rooms(body.rooms, body.repeat)

    @router.post("/settings", tags=["control"])
    async def settings(body: SettingsBody) -> dict[str, Any]:
        return await service.set_settings(**body.model_dump())

    @router.post("/dock/empty", tags=["dock"])
    async def dock_empty() -> dict[str, Any]:
        return await service.empty_dustbin()

    @router.post("/dock/wash", tags=["dock"])
    async def dock_wash() -> dict[str, Any]:
        return await service.wash_mop()

    @router.post("/consumables/{key}/reset", tags=["control"])
    async def reset_consumable(key: str) -> dict[str, Any]:
        return await service.reset_consumable(key)

    @router.post("/command", tags=["advanced"])
    async def raw_command(body: RawCommandBody) -> dict[str, Any]:
        return await service.raw_command(body.method, body.params)

    return router
