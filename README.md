# Bumper Car Arena

by Yağız Özkan & Dağhan Erdönmez

## Overview of the Game

We built a real time multiplayer bumper car game playable over a LAN. One player hosts, and others join automatically, no manual IP entry required. The host runs the authoritative physics simulation, while clients send inputs and receive game state updates at a fixed tick rate. A 3D arena is rendered using the Ursina game engine, with each car displaying its health bar and a live leaderboard showing bumps dealt and HP remaining.

## Challenges We've Encountered

The biggest challenge was designing the network architecture cleanly. We separated concerns across TCP (lobby control), UDP broadcast (auto discovery and real time game state). Auto discovery required broadcasting on the LAN and filtering responses so clients could join without configuration. We also implemented Diffie-Hellman key exchange to password-protect lobbies, which introduced ordering and timing issues in the handshake that took significant debugging to resolve.

## Shortcomings

- The game timer drifts slightly because it relies on application level timing rather than a synchronized clock.
- The focus of the project was the networking stack, not the rendering. So the visuals are a bit simple.

## Requirements

- Python 3.11+
- All machines must be on the **same local network (WiFi or LAN)**

## Setup

```bash
python -m venv venv
```

Activate the virtual environment:

- **macOS/Linux:** `source venv/bin/activate`
- **Windows:** `venv\Scripts\activate`

Then install dependencies:

```bash
pip install -r requirements.txt
```

> On some systems you may also need: `pip install screeninfo --no-deps`

## Running

```bash
python main.py
```

Choose **1** to host or **2** to join. Clients discover the host automatically — no IP address needed.

The host presses **Enter** in the lobby to start the game.

## Controls

| Key       | Action                 |
| --------- | ---------------------- |
| W / Up    | Accelerate             |
| S / Down  | Reverse                |
| A / Left  | Turn left              |
| D / Right | Turn right             |
| Enter     | Start game (host only) |
| Escape    | Quit                   |

## Rules

Ram into other cars to score bumps and reduce their HP. Last car standing wins.

## Known Bugs

Timer is not very accurate
Sometimes when you bump into a car, the bumped car can rebound and bump you back
