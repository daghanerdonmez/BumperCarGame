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
        print("\nExiting...")
        sys.exit(0)
    return val or default


def _pick_mode() -> str:
    """Return 'host' or 'client' based on argv or interactive prompt."""
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("host", "client"):
        return sys.argv[1].lower()

    print()
    print("╔══════════════════════════════╗")
    print("║       Bumper Car Arena       ║")
    print("╠══════════════════════════════╣")
    print("║  1. Host a game              ║")
    print("║  2. Join a game              ║")
    print("╚══════════════════════════════╝")
    print()
    choice = _ask("Choose [1/2]: ", "2")
    
    return "host" if choice.strip() == "1" else "client"

def _host_config() -> dict:
    print()
    print("╔══════════════════════════════╗")
    print("║      Select the Game Type    ║")
    print("╠══════════════════════════════╣")
    print("║  1. Last Standing            ║")
    print("║  2. Most Bumps               ║")
    print("╚══════════════════════════════╝")
    print()

    choice = _ask("Choose [1/2] (Default 1): ", "1").strip()
    if choice not in ("1", "2"):
        print("Invalid choice. Defaulting to Last Standing.")
        choice = "1"

    if choice == "1":
        raw_hp = _ask("How much HP should each car have? (Default 3): ", "3").strip()
        try:
            car_hp = max(1, int(raw_hp))
        except ValueError:
            print("Invalid value. Using default HP of 3.")
            car_hp = 3
        return {"score_mode": "last_standing", "car_hp": car_hp, "game_duration": 120}
    else:
        raw_dur = _ask("What should be the game duration in seconds? (Default 120): ", "120").strip()
        try:
            game_duration = max(1, int(raw_dur))
        except ValueError:
            print("Invalid value. Using default duration of 120s.")
            game_duration = 120
        return {"score_mode": "most_bumps", "game_duration": game_duration, "car_hp": 3}


def run_host(username: str) -> None:
    from host import Host
    from renderer import GameRenderer

    game_config = _host_config()

    # Print a summary so the host can confirm their settings
    print()
    mode_label = "Last Standing" if game_config["score_mode"] == "last_standing" else "Most Bumps"
    print(f"  Mode     : {mode_label}")
    if game_config["score_mode"] == "last_standing":
        print(f"  Car HP   : {game_config['car_hp']}")
    else:
        print(f"  Duration : {game_config['game_duration']}s")
    print()

    player = Host(username, game_config=game_config)
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
    default_name = "Host" if mode == "host" else "client"
    username = _ask(f"Enter your name [{default_name}]: ", default_name)

    print()
    if mode == "host":
        print(f"Starting as HOST — '{username}'")
        run_host(username)
        print("Other players can join once the window opens.")
        print("Press Enter in the lobby to start the game.")
    else:
        print(f"Starting as CLIENT — '{username}'")
        print("Searching for a host on the local network…")
        run_client(username)


if __name__ == "__main__":
    main()