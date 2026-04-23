# Network
TCP_PORT = 12488
UDP_PORT = 12489
BUFFER_SIZE = 8192
SOCKET_TIMEOUT = 1.0
DISCOVERY_INTERVAL = 5.0      # seconds between auto-broadcast ASK
DISCOVERY_BURSTS = 3          # how many ASKs per interval
DISCOVERY_BURST_DELAY = 0.2   # seconds between bursts
TCP_MSG_TIMEOUT = 2.0

# Lobby
MAX_PLAYERS = 6
LOBBY_COUNTDOWN = 3           # seconds after host hits start

# Gameplay
TICK_RATE = 30                # simulation + state-broadcast Hz
INPUT_SEND_RATE = 30          # client input send Hz

# Arena (metres, centred at origin)
ARENA_WIDTH = 20.0
ARENA_DEPTH = 20.0
WALL_THICKNESS = 0.5

# Car physics
CAR_RADIUS = 0.6              # collision radius
CAR_MASS = 1.0
CAR_MAX_SPEED = 8.0
CAR_ACCELERATION = 12.0
CAR_FRICTION = 5.0            # velocity damping per second
CAR_TURN_SPEED = 180.0        # degrees per second
BUMP_RESTITUTION = 0.7        # bounciness on car-car collision (0-1)
WALL_RESTITUTION = 0.5        # bounciness on wall hit

# Scoring
SCORE_MODE = "last_standing"  # "last_standing" | "most_bumps"
GAME_DURATION = 120           # seconds (used in most_bumps mode)
CAR_HP = 3                    # hits before a car is eliminated

# Visual (Ursina hints used by renderer)
PLAYER_COLORS = [
    (1, 0.2, 0.2),    # red
    (0.2, 0.4, 1),    # blue
    (0.2, 0.9, 0.2),  # green
    (1, 0.8, 0.1),    # yellow
    (0.9, 0.3, 0.9),  # purple
    (1, 0.5, 0.1),    # orange
]
