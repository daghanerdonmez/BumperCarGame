# Network
TCP_PORT = 12488
UDP_PORT = 12489
BUFFER_SIZE = 8192
SOCKET_TIMEOUT = 1.0
DISCOVERY_INTERVAL = 5.0      
DISCOVERY_BURSTS = 3          # how many ASKs per interval
DISCOVERY_BURST_DELAY = 0.2   # seconds between bursts
TCP_MSG_TIMEOUT = 2.0

# Lobby
MAX_PLAYERS = 6
LOBBY_COUNTDOWN = 3           

# Gameplay
TICK_RATE = 30                
INPUT_SEND_RATE = 30          

# Arena
ARENA_WIDTH = 20.0
ARENA_DEPTH = 20.0
WALL_THICKNESS = 0.5

# Car physics
CAR_RADIUS = 0.6              # collision radius
CAR_MASS = 1.0
CAR_MAX_SPEED = 20.0
CAR_ACCELERATION = 15.0
CAR_FRICTION = 5.0            
CAR_TURN_SPEED = 180.0        
BUMP_RESTITUTION = 0.7        # bounciness on car collision 
WALL_RESTITUTION = 0.5        # bounciness on wall hit

# Default values 
SCORE_MODE = "last_standing"  # "last_standing" or "most_bumps".
GAME_DURATION = 120           
CAR_HP = 3                    

# Ursina parameters
PLAYER_COLORS = [
    (1, 0.2, 0.2),    # red
    (0.2, 0.4, 1),    # blue
    (0.2, 0.9, 0.2),  # green
    (1, 0.8, 0.1),    # yellow
    (0.9, 0.3, 0.9),  # purple
    (1, 0.5, 0.1),    # orange
]
