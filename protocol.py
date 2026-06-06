"""
Packet protocol for the Bumper Car Game.

All packets are JSON objects sent as UTF-8 strings terminated with '\n' over
TCP, or as raw UTF-8 datagrams over UDP.

Packet types
------------
Discovery (UDP broadcast)
  ASK       client → broadcast   "is there a host?"
  ANNOUNCE  host   → broadcast   "I am the host"

Lobby (TCP, client ↔ host)
  JOIN_REQ      client → host   request to join
  DH_INIT       host → client   Diffie-Hellman parameters + host public key
  DH_REPLY      client → host   client public key  (shared key now established)
  PASSWORD_REQ  host → client   encrypted challenge — "send me your password"
  PASSWORD_RESP client → host   encrypted password attempt
  JOIN_ACK      host → client   accepted, carries player_id & current roster
  JOIN_DENY     host → client   rejected (wrong password / lobby full / game running)
  PLAYER_LIST   host → all      roster update (someone joined/left)
  GAME_START    host → all      game is beginning (carries initial state)

Gameplay (UDP, encrypted with 3DES after DH handshake)
  INPUT       client → host   local controls this tick
  GAME_STATE  host → all      authoritative world state this tick

Control (TCP)
  GAME_OVER   host → all      match ended, carries final scores
  DISCONNECT  either side     clean leave notification
"""

from __future__ import annotations

import hashlib
import json
import secrets

import pyDes

# ── Type constants ────────────────────────────────────────────────────────────

T_ASK        = "ASK"
T_ANNOUNCE   = "ANNOUNCE"

T_JOIN_REQ    = "JOIN_REQ"
T_JOIN_ACK    = "JOIN_ACK"
T_JOIN_DENY   = "JOIN_DENY"
T_DH_INIT     = "DH_INIT"
T_DH_REPLY    = "DH_REPLY"
T_PASSWORD_REQ  = "PASSWORD_REQ"
T_PASSWORD_RESP = "PASSWORD_RESP"
T_PLAYER_LIST = "PLAYER_LIST"
T_GAME_START  = "GAME_START"

T_INPUT      = "INPUT"
T_GAME_STATE = "GAME_STATE"

T_GAME_OVER  = "GAME_OVER"
T_DISCONNECT = "DISCONNECT"


# ── Serialisation helpers ─────────────────────────────────────────────────────

def encode(packet: dict) -> bytes:
    """Serialize a packet to UTF-8 bytes terminated with newline."""
    return (json.dumps(packet) + "\n").encode("utf-8")


def decode(raw: str | bytes) -> dict | None:
    """Parse a raw string/bytes into a packet dict, or None on error."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw.strip())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ── Cryptographic helpers (same as chat2OZKAN) ────────────────────────────────

DH_P = 15   # small for demo; swap for a proper safe-prime in production
DH_G = 2

def generate_dh_keys(p: int = DH_P, g: int = DH_G) -> tuple[int, int]:
    """Return (private_key, public_key) for a Diffie-Hellman exchange."""
    private_key = secrets.randbelow(p)
    public_key  = pow(g, private_key, p)
    return private_key, public_key

def compute_dh_key(others_public: int, my_private: int, p: int = DH_P) -> bytes:
    """Derive a 24-byte 3DES key from the shared DH secret."""
    shared = pow(others_public, my_private, p)
    return hashlib.sha256(str(shared).encode()).digest()[:24]

def evolve_key(current_key: bytes, plaintext: str) -> bytes:
    """Ratchet the session key forward after each message."""
    combined = current_key + plaintext.encode()
    return hashlib.sha256(combined).digest()[:24]

def encrypt_payload(key: bytes, plaintext: str) -> str:
    """3DES-encrypt *plaintext* and return a hex string."""
    cipher = pyDes.triple_des(key, padmode=2)
    return cipher.encrypt(plaintext).hex()

def decrypt_payload(key: bytes, hex_ciphertext: str) -> str:
    """3DES-decrypt a hex string and return the plaintext."""
    cipher = pyDes.triple_des(key, padmode=2)
    return cipher.decrypt(bytes.fromhex(hex_ciphertext), padmode=2).decode("utf-8")


# ── Discovery ─────────────────────────────────────────────────────────────────

def mk_ask(sender_ip: str) -> dict:
    return {"type": T_ASK, "sender_ip": sender_ip}


def mk_announce(host_name: str, host_ip: str, player_count: int, max_players: int,
                has_password: bool = False) -> dict:
    return {
        "type": T_ANNOUNCE,
        "host_name": host_name,
        "host_ip": host_ip,
        "player_count": player_count,
        "max_players": max_players,
        "has_password": has_password,
    }


# ── Lobby ─────────────────────────────────────────────────────────────────────

def mk_join_req(player_name: str, player_ip: str) -> dict:
    return {"type": T_JOIN_REQ, "player_name": player_name, "player_ip": player_ip}


def mk_join_ack(player_id: int, player_name: str, roster: list[dict]) -> dict:
    """
    roster: list of {"id": int, "name": str, "ip": str}
    """
    return {
        "type": T_JOIN_ACK,
        "player_id": player_id,
        "player_name": player_name,
        "roster": roster,
    }


def mk_join_deny(reason: str) -> dict:
    return {"type": T_JOIN_DENY, "reason": reason}


def mk_dh_init(g: int, p: int, public_key: int) -> dict:
    """Host → client: start a Diffie-Hellman handshake."""
    return {"type": T_DH_INIT, "g": g, "p": p, "public_key": public_key}

def mk_dh_reply(public_key: int) -> dict:
    """Client → host: send client's DH public key."""
    return {"type": T_DH_REPLY, "public_key": public_key}


def mk_password_req(encrypted_challenge: str) -> dict:
    """Host → client: encrypted prompt to send the lobby password."""
    return {"type": T_PASSWORD_REQ, "data": encrypted_challenge}

def mk_password_resp(encrypted_password: str) -> dict:
    """Client → host: encrypted password attempt."""
    return {"type": T_PASSWORD_RESP, "data": encrypted_password}


def mk_player_list(roster: list[dict]) -> dict:
    """roster: list of {"id": int, "name": str, "ip": str}"""
    return {"type": T_PLAYER_LIST, "roster": roster}


def mk_game_start(roster: list[dict], initial_positions: list[dict],
                  score_mode: str = 'last_standing',
                  game_duration: float = 120.0,
                  car_hp: int = 3) -> dict:
    """
    initial_positions: list of {"id": int, "x": float, "z": float, "angle": float}
    """
    return {
        "type": T_GAME_START,
        "roster": roster,
        "initial_positions": initial_positions,
        "score_mode": score_mode,
        "game_duration": game_duration,
        "car_hp": car_hp,
    }


# ── Gameplay ──────────────────────────────────────────────────────────────────

def mk_input(player_id: int, tick: int, forward: float, turn: float) -> dict:
    """
    forward: -1.0 (reverse) … +1.0 (accelerate)
    turn:    -1.0 (left)    … +1.0 (right)
    """
    return {
        "type": T_INPUT,
        "player_id": player_id,
        "tick": tick,
        "forward": forward,
        "turn": turn,
    }


def mk_game_state(tick: int, cars: list[dict]) -> dict:
    """
    cars: list of {
        "id":    int,
        "x":     float,
        "z":     float,
        "angle": float,   # degrees, 0 = +Z axis
        "vx":    float,
        "vz":    float,
        "hp":    int,
        "score": int,
        "alive": bool,
    }
    """
    return {"type": T_GAME_STATE, "tick": tick, "cars": cars}


# ── Control ───────────────────────────────────────────────────────────────────

def mk_game_over(scores: list[dict]) -> dict:
    """scores: list of {"id": int, "name": str, "score": int, "winner": bool}"""
    return {"type": T_GAME_OVER, "scores": scores}


def mk_disconnect(player_id: int, player_name: str) -> dict:
    return {"type": T_DISCONNECT, "player_id": player_id, "player_name": player_name}