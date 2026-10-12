"""Command-line helpers: `python -m app.cli login|logout|devices|status`.

In Docker: `docker compose run --rm roborock python -m app.cli login`, then
`docker compose restart roborock` so the running server picks up the credentials.
"""

import argparse
import asyncio
import json
import sys

from roborock.exceptions import RoborockException

from .auth import CredentialStore, LoginFlow
from .config import get_settings
from .robot import RobotError, RobotService, RobotState


async def _login(email: str | None) -> int:
    settings = get_settings()
    email = email or input(f"Roborock account email [{settings.roborock_email}]: ").strip() or settings.roborock_email
    if not email:
        print("An email address is required.", file=sys.stderr)
        return 1
    flow = LoginFlow()
    try:
        await flow.request_code(email)
        code = input("Code from the email: ").strip()
        params = await flow.complete(email, code)
    except RoborockException as err:
        print(f"Login failed: {err}", file=sys.stderr)
        return 1
    store = CredentialStore(settings.credentials_path)
    store.save(params)
    settings.cache_path.unlink(missing_ok=True)
    print(f"Saved credentials to {store.path}. Restart the server to use them.")
    return 0


async def _with_service(action: str) -> int:
    settings = get_settings()
    service = RobotService(settings)
    await service.start()
    try:
        for _ in range(60):
            if service.state != RobotState.CONNECTING:
                break
            await asyncio.sleep(0.5)
        if action == "devices":
            result = await service.devices()
        else:
            for _ in range(30):
                if service.health()["device_connected"]:
                    break
                await asyncio.sleep(0.5)
            result = {"health": service.health(), "status": await service.status(force=True)}
        print(json.dumps(result, indent=2, default=str))
        return 0
    except RobotError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="log in with an email code and store credentials")
    login.add_argument("--email")
    sub.add_parser("logout", help="delete stored credentials and device cache")
    sub.add_parser("devices", help="list devices on the account")
    sub.add_parser("status", help="print the selected robot's status")
    args = parser.parse_args()

    if args.command == "login":
        sys.exit(asyncio.run(_login(args.email)))
    if args.command == "logout":
        settings = get_settings()
        CredentialStore(settings.credentials_path).delete()
        settings.cache_path.unlink(missing_ok=True)
        print("Credentials removed.")
        return
    sys.exit(asyncio.run(_with_service(args.command)))


if __name__ == "__main__":
    main()
