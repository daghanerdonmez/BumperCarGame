"""
Packet details are in protocol.py
- send UDP ASK broadcasts, wait for ANNOUNCE
- TCP connect to host, send JOIN_REQ, wait for JOIN_ACK
- receive PLAYER_LIST updates over TCP
- send INPUT via UDP each tick, receive GAME_STATE via UDP
- Game over: display final scores finish network activity

Main loop reads game_phase / roster / get_sim_state() each frame, and sends input via set_input()
"""
from __future__ import annotations

import json
import select
import socket
import threading
import time

from config import (
    TCP_PORT, UDP_PORT, BUFFER_SIZE,
    INPUT_SEND_RATE, DISCOVERY_INTERVAL, DISCOVERY_BURSTS,
    DISCOVERY_BURST_DELAY, CAR_HP,
)
from protocol import (
    encode, decode,
    T_ANNOUNCE, T_JOIN_ACK, T_JOIN_DENY, T_PLAYER_LIST,
    T_GAME_START, T_GAME_STATE, T_GAME_OVER, T_DISCONNECT,
    T_DH_INIT, T_PASSWORD_REQ,
    mk_ask, mk_join_req, mk_input, mk_disconnect,
    mk_dh_reply, mk_password_resp,
    generate_dh_keys, compute_dh_key, evolve_key,
    encrypt_payload, decrypt_payload,
    DH_P, DH_G,
)


class Client:
    def __init__(self, player_name: str):
        self.player_name = player_name

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        self.my_ip = s.getsockname()[0]
        s.close()

        # Phases: "discovering" | "joining" | "lobby" | "game" | "over"
        self.game_phase  = "discovering"
        self._phase_lock = threading.Lock()

        # Set when ANNOUNCE is received
        self.host_ip:   str | None = None
        self.host_name: str | None = None
        self._host_found = threading.Event()

        self.discovered_hosts: list[dict] = []
        self._discovered_lock = threading.Lock()

        # Assigned by host when join is acked
        self.player_id: int | None = None

        self.roster: list[dict] = []

        # To store the updates to GAME_STATE cars list
        self._latest_state: list[dict] = []
        self._state_lock = threading.Lock()

        # Input data
        self._current_forward = 0.0
        self._current_turn    = 0.0
        self._input_lock      = threading.Lock()
        self._local_tick      = 0

        self.final_scores: list[dict] = []

        # Game settings received in GAME_START
        self.score_mode:    str   = "last_standing"
        self.game_duration: float = 120.0

        self._password: str = ""

        # DH keys
        self._session_key: bytes | None = None
        self._session_key_lock = threading.Lock()

        # Round-trip ping
        self.ping_ms: int | None = None

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

        self._tcp_conn: socket.socket | None = None
        self._tcp_lock = threading.Lock()
        self._udp_sock: socket.socket | None = None


    def start(self):
        self._spawn(self._udp_listen_loop, "client-udp")
        self._spawn(self._discover_loop,   "client-discover")
        print(f"[client] Started as '{self.player_name}' @ {self.my_ip}")

    def stop(self):
        # Send DISCONNECT when closing
        if self.player_id is not None:
            self._send_tcp(mk_disconnect(self.player_id, self.player_name))

        self._stop_event.set()

        with self._tcp_lock:
            if self._tcp_conn:
                try:
                    self._tcp_conn.close()
                except OSError:
                    pass

        if self._udp_sock:
            try:
                self._udp_sock.close()
            except OSError:
                pass

        for t in self._threads:
            t.join(timeout=2.0)

    def confirm_join(self, host_ip: str | None = None, password: str = ""):
        with self._discovered_lock:
            hosts = list(self.discovered_hosts)

        if host_ip is None and hosts:
            host_ip = hosts[0]["host_ip"]

        if host_ip is None:
            return  # nothing found yet

        # Find the matching entry so we have host_name too
        with self._discovered_lock:
            entry = next((h for h in self.discovered_hosts if h["host_ip"] == host_ip), None)

        self.host_ip   = host_ip
        self.host_name = entry["host_name"] if entry else host_ip
        self._password = password
        self._host_found.set()

    def set_input(self, forward: float, turn: float):
        # Called every frame with the local players controls
        with self._input_lock:
            self._current_forward = max(-1.0, min(1.0, forward))
            self._current_turn    = max(-1.0, min(1.0, turn))

    def get_sim_state(self) -> list[dict]:
        with self._state_lock:
            return list(self._latest_state)

    # ── Thread helpers ────────────────────────────────────────────────────────

    def _spawn(self, target, name: str):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _discover_loop(self):
        while not self._stop_event.is_set():
            with self._phase_lock:
                phase = self.game_phase
            if phase != "discovering":
                return

            for _ in range(DISCOVERY_BURSTS):
                self._send_ask()
                time.sleep(DISCOVERY_BURST_DELAY)

            # Wait for UDP listener to signal a host was found
            if self._host_found.wait(timeout=DISCOVERY_INTERVAL):
                break

        if self._host_found.is_set() and not self._stop_event.is_set():
            self._join_host()

    def _send_ask(self):
        raw = json.dumps(mk_ask(self.my_ip)).encode("utf-8")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(raw, ("<broadcast>", UDP_PORT))
        except OSError:
            pass

    # ── Join and lobby ──────────────────────────────────────────────────────

    def _join_host(self):
        with self._phase_lock:
            self.game_phase = "joining"

        print(f"[client] Connecting to host '{self.host_name}' @ {self.host_ip} …")
        try:
            conn = socket.create_connection((self.host_ip, TCP_PORT), timeout=5.0)
        except OSError as e:
            print(f"[client] TCP connect failed: {e}")
            with self._phase_lock:
                self.game_phase = "discovering"
            self._host_found.clear()
            return
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        conn.settimeout(None)   # back to blocking mode after create_connection

        with self._tcp_lock:
            self._tcp_conn = conn

        conn.sendall(encode(mk_join_req(self.player_name, self.my_ip)))
        self._spawn(self._tcp_read_loop, "client-tcp-reader")

    def _tcp_read_loop(self):
        # Use select with a 1 s timeout so the thread wakes periodically to 
        # check _stop_event rather than blocking forever on an idle connection

        with self._tcp_lock:
            conn = self._tcp_conn

        buf = b""
        try:
            while not self._stop_event.is_set():
                try:
                    ready = select.select([conn], [], [], 1.0)
                except OSError:
                    break
                if not ready[0]:
                    continue   
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break   # host closed connection

                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    pkt = decode(line)
                    if pkt:
                        self._handle_tcp_packet(pkt)
        finally:
            with self._phase_lock:
                phase = self.game_phase
            if phase not in ("over",):
                print("[client] Lost connection to host")

    def _handle_tcp_packet(self, pkt: dict):
        t = pkt.get("type")

        if t == T_DH_INIT:
            # we are Bob
            g = pkt.get("g", DH_G)
            p = pkt.get("p", DH_P)
            A = pkt.get("public_key", 0)
            b, B = generate_dh_keys(p, g)
            key = compute_dh_key(A, b, p)
            with self._session_key_lock:
                self._session_key = key
            self._dh_p = p
            self._send_tcp(mk_dh_reply(B))

        elif t == T_PASSWORD_REQ:
            with self._session_key_lock:
                key = self._session_key
            if key is None:
                return
            try:
                decrypt_payload(key, pkt["data"])  
                new_key = evolve_key(key, "PASSWORD_REQUIRED")
            except Exception:
                return

            pw        = self._password
            encrypted = encrypt_payload(new_key, pw)
            final_key = evolve_key(new_key, pw)
            with self._session_key_lock:
                self._session_key = final_key
            self._send_tcp(mk_password_resp(encrypted))

        elif t == T_JOIN_ACK:
            self.player_id = pkt["player_id"]
            self.roster    = pkt["roster"]
            with self._phase_lock:
                self.game_phase = "lobby"
            print(f"[client] Joined lobby as '{self.player_name}' (id={self.player_id})")
            print(f"[client] Current players: {[p['name'] for p in self.roster]}")
            print(f"[client] Secure channel established with host")

        elif t == T_JOIN_DENY:
            print(f"[client] Join denied: {pkt.get('reason', '?')}")
            with self._phase_lock:
                self.game_phase = "discovering"
            self.host_ip   = None
            self.host_name = None
            self._host_found.clear()
            self._spawn(self._discover_loop, "client-discover-retry")

        elif t == T_PLAYER_LIST:
            self.roster = pkt["roster"]
            print(f"[client] Lobby update: {[p['name'] for p in self.roster]}")

        elif t == T_GAME_START:
            self.roster       = pkt["roster"]
            self.score_mode    = pkt.get("score_mode", "last_standing")
            self.game_duration = float(pkt.get("game_duration", 120.0))
            # Placeholder so the renderer has something before first packet
            with self._state_lock:
                self._latest_state = [
                    {
                        "id":    pos["id"],
                        "x":     pos["x"],
                        "z":     pos["z"],
                        "angle": pos["angle"],
                        "vx":    0.0,
                        "vz":    0.0,
                        "hp":    CAR_HP,
                        "score": 0,
                        "alive": True,
                    }
                    for pos in pkt["initial_positions"]
                ]
            with self._phase_lock:
                self.game_phase = "game"
            self._spawn(self._input_send_loop, "client-input-send")
            print("[client] Game started!")

        elif t == T_GAME_OVER:
            self.final_scores = pkt["scores"]
            with self._phase_lock:
                self.game_phase = "over"
            print("[client] ── Game Over ──────────────────")
            for row in self.final_scores:
                marker = "★" if row["winner"] else " "
                print(f"[client]  {marker} {row['name']:15s}  score={row['score']}")

    def _send_tcp(self, pkt: dict):
        with self._tcp_lock:
            conn = self._tcp_conn
        if conn:
            try:
                conn.sendall(encode(pkt))
            except OSError:
                pass

    # ── UDP ───────────────────────────────────────────────────────────────────

    def _udp_listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", UDP_PORT))
        s.setblocking(False)
        self._udp_sock = s

        try:
            while not self._stop_event.is_set():
                try:
                    ready = select.select([s], [], [], 1.0)
                except OSError:
                    break
                if not ready[0]:
                    continue
                try:
                    data, addr = s.recvfrom(BUFFER_SIZE)
                except OSError:
                    break

                pkt = decode(data)
                if pkt is None:
                    continue

                self._handle_udp_packet(pkt, addr[0])
        finally:
            self._udp_sock = None
            s.close()

    def _handle_udp_packet(self, pkt: dict, sender_ip: str):
        t = pkt.get("type")

        if t == T_ANNOUNCE:
            host_ip = pkt.get("host_ip", sender_ip)
            if host_ip == self.my_ip:
                return   # ignore our own machine 
            entry = {
                "host_ip":      host_ip,
                "host_name":    pkt.get("host_name", "?"),
                "player_count": pkt.get("player_count", 0),
                "max_players":  pkt.get("max_players", 0),
                "has_password": pkt.get("has_password", False),
            }
            with self._discovered_lock:
                ips = [h["host_ip"] for h in self.discovered_hosts]
                if host_ip not in ips:
                    self.discovered_hosts.append(entry)
                    print(f"[client] Found host '{entry['host_name']}' @ {host_ip} "
                          f"({entry['player_count']}/{entry['max_players']} players)")
                else:
                    for h in self.discovered_hosts:
                        if h["host_ip"] == host_ip:
                            h.update(entry)

        elif t == T_GAME_STATE:
            with self._phase_lock:
                phase = self.game_phase
            if phase == "game":
                with self._state_lock:
                    self._latest_state = pkt["cars"]
                sent_at = pkt.get("sent_at")
                if sent_at is not None:
                    self.ping_ms = round((time.time() - sent_at) * 1000)

    # ── Input sender ──────────────────────────────────────────────────────────

    def _input_send_loop(self):
        # Send INPUT packets to the host 30 times per tick
        dt        = 1.0 / INPUT_SEND_RATE
        next_send = time.perf_counter() + dt

        while not self._stop_event.is_set():
            with self._phase_lock:
                phase = self.game_phase
            if phase != "game":
                break

            now = time.perf_counter()
            if now < next_send:
                time.sleep(next_send - now)
            next_send += dt

            with self._input_lock:
                forward = self._current_forward
                turn    = self._current_turn

            self._local_tick += 1
            pkt = mk_input(self.player_id, self._local_tick, forward, turn)
            pkt["sent_at"] = time.perf_counter()   # used for ping calculation

            raw = json.dumps(pkt).encode("utf-8")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(raw, (self.host_ip, UDP_PORT))
            except OSError:
                pass