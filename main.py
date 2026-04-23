"""
Bumper Car Arena — entry point.

Usage
-----
  python main.py          # interactive prompt
  python main.py host     # go straight to host mode
  python main.py client   # go straight to client mode

Controls (in-game)
------------------
  W / Up     : accelerate
  S / Down   : reverse
  A / Left   : turn left
  D / Right  : turn right
  Enter      : start game (host only, in lobby)
  Escape     : quit
"""
from __future__ import annotations

import sys


def _ask(prompt: str, default: str = "") -> str:
    try:
        val = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        val = ""
    return val or default


def _pick_mode() -> str:
    """Return 'host' or 'client' based on argv or interactive prompt."""
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("host", "client"):
        return sys.argv[1].lower()

    print()
    print("╔══════════════════════════════╗")
    print("║    Bumper Car Arena  🚗💥    ║")
    print("╠══════════════════════════════╣")
    print("║  1. Host a game              ║")
    print("║  2. Join a game              ║")
    print("╚══════════════════════════════╝")
    print()
    choice = _ask("Choose [1/2]: ", "2")
    return "host" if choice.strip() == "1" else "client"


def run_host(username: str) -> None:
    from host import Host
    from renderer import GameRenderer

    player = Host(username)
    player.start()

    renderer = GameRenderer(player, is_host=True)
    try:
        renderer.run()          # blocks until window closes
    finally:
        player.stop()


def run_client(username: str) -> None:
    from client import Client
    from renderer import GameRenderer

    player = Client(username)
    player.start()              # begins discovery immediately

    renderer = GameRenderer(player, is_host=False)
    try:
        renderer.run()          # blocks until window closes
    finally:
        player.stop()


def main() -> None:
    mode = _pick_mode()

    print()
    default_name = "Host" if mode == "host" else "Player"
    username = _ask(f"Enter your name [{default_name}]: ", default_name)

    print()
    if mode == "host":
        print(f"Starting as HOST — '{username}'")
        print("Other players can join once the window opens.")
        print("Press  Enter  in the lobby to start the game.")
        run_host(username)
    else:
        print(f"Starting as CLIENT — '{username}'")
        print("Searching for a host on the local network…")
        run_client(username)


if __name__ == "__main__":
    main()
