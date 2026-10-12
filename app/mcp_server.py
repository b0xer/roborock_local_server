"""MCP server exposing robot control as tools (served over streamable HTTP at /mcp)."""

from collections.abc import Awaitable
from typing import Any, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .robot import RobotError, RobotService

T = TypeVar("T")

READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False)

INSTRUCTIONS = """\
Controls a Roborock robot vacuum/mop on the local network.
Call get_status first to see what the robot is doing. Use list_rooms to discover room names
before clean_rooms. Commands return quickly; poll get_status to follow progress.
"""


async def _guard(call: Awaitable[T]) -> T:
    """Surface robot errors to the agent as readable tool errors."""
    try:
        return await call
    except RobotError as err:
        raise ToolError(str(err)) from err


def build_mcp(service: RobotService, allow_raw_commands: bool = False) -> MCPServer:
    mcp = MCPServer("roborock", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ)
    async def get_status() -> dict[str, Any]:
        """Current robot state: activity, battery %, errors, cleaning progress and active cleaning settings."""
        return await _guard(service.status(force=True))

    @mcp.tool(annotations=READ)
    async def list_rooms() -> list[dict[str, Any]]:
        """Rooms on the current map with their segment IDs. Use these names or IDs with clean_rooms."""
        return await _guard(service.rooms())

    @mcp.tool(annotations=ACT)
    async def start_cleaning() -> dict[str, Any]:
        """Start (or resume) a full-home clean."""
        return await _guard(service.start_cleaning())

    @mcp.tool(annotations=ACT)
    async def pause_cleaning() -> dict[str, Any]:
        """Pause the current cleaning job. It can be resumed with start_cleaning."""
        return await _guard(service.pause())

    @mcp.tool(annotations=ACT)
    async def stop_cleaning() -> dict[str, Any]:
        """Stop the current cleaning job (the robot stays where it is)."""
        return await _guard(service.stop_cleaning())

    @mcp.tool(annotations=ACT)
    async def return_to_dock() -> dict[str, Any]:
        """Send the robot back to its dock to charge."""
        return await _guard(service.dock())

    @mcp.tool(annotations=ACT)
    async def locate_robot() -> dict[str, Any]:
        """Make the robot play a sound so it can be found."""
        return await _guard(service.locate())

    @mcp.tool(annotations=ACT)
    async def clean_rooms(rooms: list[str], repeat: int = 1) -> dict[str, Any]:
        """Clean specific rooms.

        Args:
            rooms: Room names (case-insensitive, as returned by list_rooms) or segment IDs.
            repeat: Number of cleaning passes, 1-3.
        """
        return await _guard(service.clean_rooms(rooms, repeat))

    @mcp.tool(annotations=READ)
    async def get_cleaning_options() -> dict[str, list[str]]:
        """Values supported by this robot for set_cleaning_mode (cleaning modes, fan speeds, water levels, mop routes)."""
        return await _guard(service.options())

    @mcp.tool(annotations=ACT)
    async def set_cleaning_mode(
        cleaning_mode: str | None = None,
        fan_speed: str | None = None,
        water_level: str | None = None,
        mop_route: str | None = None,
    ) -> dict[str, Any]:
        """Change cleaning settings. Only the arguments you pass are changed.

        Valid values come from get_cleaning_options. cleaning_mode (vacuum / vac_and_mop / mop)
        is the high-level switch; fan_speed, water_level and mop_route fine-tune it.
        """
        return await _guard(service.set_settings(cleaning_mode, fan_speed, water_level, mop_route))

    @mcp.tool(annotations=READ)
    async def get_consumables() -> list[dict[str, Any]]:
        """Wear of brushes, filter, sensors and dock parts, with remaining life in percent."""
        return await _guard(service.consumables())

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def reset_consumable(consumable: str) -> dict[str, Any]:
        """Reset a consumable's wear counter after replacing or cleaning it (key from get_consumables)."""
        return await _guard(service.reset_consumable(consumable))

    @mcp.tool(annotations=READ)
    async def get_cleaning_summary() -> dict[str, Any]:
        """Lifetime cleaning totals and details of the most recent clean."""
        return await _guard(service.clean_summary())

    @mcp.tool(annotations=ACT)
    async def empty_dustbin() -> dict[str, Any]:
        """Have the dock empty the robot's dustbin (robot must be docked)."""
        return await _guard(service.empty_dustbin())

    @mcp.tool(annotations=ACT)
    async def wash_mop() -> dict[str, Any]:
        """Have the dock wash the mop pads (robot must be docked, wash-capable docks only)."""
        return await _guard(service.wash_mop())

    @mcp.tool(annotations=READ)
    async def get_map() -> Image:
        """Rendered PNG image of the current map, including the robot position and cleaned path."""
        return Image(data=await _guard(service.map_png()), format="png")

    if allow_raw_commands:

        @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
        async def send_raw_command(method: str, params: Any = None) -> dict[str, Any]:
            """Send a raw Roborock V1 command (e.g. method='get_dnd_timer'). For advanced use only."""
            return await _guard(service.raw_command(method, params))

    return mcp
