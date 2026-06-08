# Bumper Car Arena

Multiplayer LAN bumper car game for CMPE 487. One player hosts, others join automatically over the local network.

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
