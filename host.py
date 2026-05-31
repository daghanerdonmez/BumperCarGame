"""
Host networking for the Bumper Car Game.

Responsibilities:
  - UDP broadcast: respond to ASK, periodically ANNOUNCE own presence
  - TCP lobby: accept JOIN_REQ, maintain persistent connections for pushing
    PLAYER_LIST / GAME_START / GAME_OVER to all clients
  - UDP game: receive INPUT packets, broadcast GAME_STATE each tick
  - Run the authoritative Simulation

The Host is itself a player (player_id = 1).  The renderer / main thread
calls start(), then start_game() when ready, then reads state each frame
via get_sim_state() and pushes its own input via apply_local_input().
"""
from __future__ import annotations

import json
import select
import socket
import threading
import time

from config import (
    TCP_PORT, UDP_PORT, BUFFER_SIZE,
    MAX_PLAYERS, TICK_RATE, DISCOVERY_INTERVAL,
)
from protocol import (
    encode, decode,
    T_ASK, T_INPUT, T_DISCONNECT,
    mk_announce, mk_join_ack, mk_join_deny,
    mk_player_list, mk_game_start, mk_game_state, mk_game_over,
)
from simulation import Simulation


class Host:
    def __init__(self, player_name: str, game_config: dict | None = None):
        self.player_name  = player_name
        self.my_player_id = 1

        # Game settings chosen by the host before starting
        cfg = game_config or {}
        self.score_mode    = cfg.get("score_mode",    "last_standing")
        self.game_duration = float(cfg.get("game_duration", 120))
        self.car_hp        = int(cfg.get("car_hp", 3))

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        self.my_ip = s.getsockname()[0]
        s.close()

        # "lobby" | "game" | "over"
        self.game_phase      = "lobby"
        self._phase_lock     = threading.Lock()

        # Roster: list of {"id": int, "name": str, "ip": str}
        self.roster          = []
        self._roster_lock    = threading.Lock()
        self._next_id        = 2   # host is 1, guests start at 2

        # Persistent TCP connections to clients {player_id: socket}
        self._client_conns      = {}
        self._client_conns_lock = threading.Lock()

        # Client IPs for UDP game state delivery {player_id: ip_str}
        self._client_ips      = {}
        self._client_ips_lock = threading.Lock()

        # Simulation (created at start_game)
        self.sim: Simulation | None = None

        # Final scores set when game ends
        self.final_scores: list[dict] = []

        self._stop_event      = threading.Event()
        self._threads: list[threading.Thread] = []
        self._tcp_server_sock: socket.socket | None = None
        self._udp_sock:        socket.socket | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Add self to roster, open sockets, spawn background threads."""
        with self._roster_lock:
            self.roster.append({
                "id":   self.my_player_id,
                "name": self.player_name,
                "ip":   self.my_ip,
            })

        self._spawn(self._tcp_listen_loop,   "host-tcp-listen")
        self._spawn(self._udp_listen_loop,   "host-udp-listen")
        self._spawn(self._auto_announce_loop, "host-announce")

        print(f"[host] Started as '{self.player_name}' @ {self.my_ip}")
        print(f"[host] TCP:{TCP_PORT}  UDP:{UDP_PORT}")

    def stop(self):
        """Signal all threads to stop and wait for them."""
        self._stop_event.set()
        for sock in (self._tcp_server_sock, self._udp_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        for t in self._threads:
            t.join(timeout=2.0)

    def start_game(self) -> bool:
        """
        Transition lobby → game.  Creates the Simulation, sends GAME_START to
        all clients, and spawns the fixed-rate game loop thread.
        Returns False if already in game/over.
        """
        with self._phase_lock:
            if self.game_phase != "lobby":
                return False
            self.game_phase = "game"

        with self._roster_lock:
            roster_snap = list(self.roster)

        self.sim = Simulation(
            roster_snap,
            score_mode=self.score_mode,
            game_duration=self.game_duration,
            car_hp=self.car_hp,
        )
        positions = self.sim.get_initial_positions()

        start_pkt = mk_game_start(roster_snap, positions)
        self._broadcast_tcp(start_pkt)

        self._spawn(self._game_loop, "host-game-loop")
        print(f"[host] Game started with {len(roster_snap)} players")
        return True

    def apply_local_input(self, forward: float, turn: float):
        """Called each frame by the renderer for the host's own car."""
        if self.sim is not None:
            self.sim.apply_input(self.my_player_id, forward, turn)

    def get_sim_state(self) -> list[dict]:
        """Returns current car-state list for the renderer to consume."""
        if self.sim is not None:
            return self.sim.get_car_states()
        return []

    # ── Thread helpers ────────────────────────────────────────────────────────

    def _spawn(self, target, name: str):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    # ── TCP lobby ─────────────────────────────────────────────────────────────

    def _tcp_listen_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", TCP_PORT))
            srv.listen()
            srv.settimeout(1.0)
            self._tcp_server_sock = srv

            while not self._stop_event.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._spawn(
                    lambda c=conn, a=addr[0]: self._handle_client_conn(c, a),
                    f"host-client-{addr[0]}"
                )

        self._tcp_server_sock = None

    def _handle_client_conn(self, conn: socket.socket, client_ip: str):
        """
        Persistent handler for one client TCP connection.
        Reads JOIN_REQ, sends JOIN_ACK, then waits for DISCONNECT or close.
        """
        with conn:
            # ── Phase 1: read JOIN_REQ ────────────────────────────────────
            raw = self._recv_line(conn)
            if raw is None:
                return

            pkt = decode(raw)
            if pkt is None or pkt.get("type") != "JOIN_REQ":
                return

            # ── Phase 2: admission check ──────────────────────────────────
            with self._phase_lock:
                phase = self.game_phase
            if phase != "lobby":
                conn.sendall(encode(mk_join_deny("Game already in progress")))
                return

            with self._roster_lock:
                if len(self.roster) >= MAX_PLAYERS:
                    conn.sendall(encode(mk_join_deny("Lobby is full")))
                    return

                player_id   = self._next_id
                self._next_id += 1
                player_name = pkt.get("player_name", f"Player{player_id}")
                player_ip   = pkt.get("player_ip", client_ip)

                self.roster.append({"id": player_id, "name": player_name, "ip": player_ip})
                roster_snap = list(self.roster)

            with self._client_conns_lock:
                self._client_conns[player_id] = conn
            with self._client_ips_lock:
                self._client_ips[player_id] = player_ip

            print(f"[host] '{player_name}' ({player_ip}) joined → id={player_id}")

            # ── Phase 3: send ACK, notify others ─────────────────────────
            conn.sendall(encode(mk_join_ack(player_id, player_name, roster_snap)))
            self._broadcast_tcp(mk_player_list(roster_snap), exclude_id=player_id)

            # ── Phase 4: keep connection alive (wait for DISCONNECT) ──────
            while not self._stop_event.is_set():
                raw = self._recv_line(conn)
                if raw is None:
                    break  # client closed connection
                inner = decode(raw)
                if inner and inner.get("type") == T_DISCONNECT:
                    break

        # ── Cleanup ───────────────────────────────────────────────────────
        self._handle_disconnect(player_id)

    def _recv_line(self, conn: socket.socket) -> str | None:
        """Read bytes until '\\n', return stripped string or None on disconnect."""
        chunks = []
        try:
            conn.settimeout(None)   # block until data or close
            while True:
                data = conn.recv(4096)
                if not data:
                    return None
                chunks.append(data)
                if b"\n" in data:
                    break
        except OSError:
            return None
        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    def _send_tcp(self, player_id: int, packet: dict):
        """Send a packet to one client over TCP."""
        with self._client_conns_lock:
            conn = self._client_conns.get(player_id)
        if conn:
            try:
                conn.sendall(encode(packet))
            except OSError:
                pass

    def _broadcast_tcp(self, packet: dict, exclude_id: int | None = None):
        """Push a packet to every connected client except exclude_id."""
        raw = encode(packet)
        with self._client_conns_lock:
            targets = [(pid, sock)
                       for pid, sock in self._client_conns.items()
                       if pid != exclude_id]
        for pid, sock in targets:
            try:
                sock.sendall(raw)
            except OSError:
                pass

    def _handle_disconnect(self, player_id: int):
        with self._client_conns_lock:
            self._client_conns.pop(player_id, None)
        with self._client_ips_lock:
            self._client_ips.pop(player_id, None)

        with self._phase_lock:
            phase = self.game_phase

        if phase == "lobby":
            with self._roster_lock:
                self.roster = [p for p in self.roster if p["id"] != player_id]
                roster_snap = list(self.roster)
            self._broadcast_tcp(mk_player_list(roster_snap))
            print(f"[host] Player {player_id} left lobby")
        else:
            # During game: eliminate the car but keep them in the roster display
            if self.sim:
                car = self.sim.cars.get(player_id)
                if car and car.alive:
                    car.alive = False
                    print(f"[host] Player {player_id} disconnected mid-game → eliminated")

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

                t = pkt.get("type")
                sender_ip = addr[0]

                if t == T_ASK:
                    if pkt.get("sender_ip") != self.my_ip:
                        self._send_udp(sender_ip, self._make_announce())

                elif t == T_INPUT:
                    with self._phase_lock:
                        phase = self.game_phase
                    if phase == "game" and self.sim is not None:
                        self.sim.apply_input(
                            pkt.get("player_id", -1),
                            pkt.get("forward", 0.0),
                            pkt.get("turn",    0.0),
                        )
        finally:
            self._udp_sock = None
            s.close()

    def _send_udp(self, ip: str, packet: dict):
        raw = json.dumps(packet).encode("utf-8")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(raw, (ip, UDP_PORT))
        except OSError:
            pass

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _make_announce(self) -> dict:
        with self._roster_lock:
            count = len(self.roster)
        return mk_announce(self.player_name, self.my_ip, count, MAX_PLAYERS)

    def _auto_announce_loop(self):
        """Broadcast ANNOUNCE periodically while in lobby so clients can find us."""
        while not self._stop_event.is_set():
            with self._phase_lock:
                phase = self.game_phase
            if phase == "lobby":
                raw = json.dumps(self._make_announce()).encode("utf-8")
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        s.sendto(raw, ("<broadcast>", UDP_PORT))
                except OSError as e:
                    print(f"[host] broadcast error: {e}")

            # Sleep in short steps so stop_event triggers promptly
            for _ in range(int(DISCOVERY_INTERVAL * 10)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    # ── Game loop ─────────────────────────────────────────────────────────────

    def _game_loop(self):
        dt         = 1.0 / TICK_RATE
        next_tick  = time.perf_counter() + dt

        while not self._stop_event.is_set():
            now = time.perf_counter()
            if now < next_tick:
                time.sleep(next_tick - now)
            next_tick += dt

            self.sim.step(dt)

            state_pkt = mk_game_state(self.sim.tick, self.sim.get_car_states())
            raw_state  = json.dumps(state_pkt).encode("utf-8")

            with self._client_ips_lock:
                ips = list(self._client_ips.values())

            for ip in ips:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.sendto(raw_state, (ip, UDP_PORT))
                except OSError:
                    pass

            if not self.sim.running:
                self._end_game()
                break

    def _end_game(self):
        with self._phase_lock:
            self.game_phase = "over"

        self.final_scores = self.sim.get_scores()
        self._broadcast_tcp(mk_game_over(self.final_scores))

        print("[host] ── Game Over ──────────────────")
        for row in self.final_scores:
            marker = "★" if row["winner"] else " "
            print(f"[host]  {marker} {row['name']:15s}  score={row['score']}")