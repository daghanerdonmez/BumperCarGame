"""
Pure game simulation — no networking, no rendering.
The host creates one Simulation instance and calls step(dt) each tick.
"""
from __future__ import annotations

import math
from config import (
    ARENA_WIDTH, ARENA_DEPTH,
    CAR_RADIUS, CAR_MAX_SPEED, CAR_ACCELERATION,
    CAR_FRICTION, CAR_TURN_SPEED,
    BUMP_RESTITUTION, WALL_RESTITUTION,
    CAR_HP, SCORE_MODE, GAME_DURATION,
)

_HALF_W = ARENA_WIDTH  / 2 - CAR_RADIUS   # x boundary for car centre
_HALF_D = ARENA_DEPTH  / 2 - CAR_RADIUS   # z boundary for car centre
_MIN_BUMP_SPEED = 1.0                       # m/s approach speed to score a bump


class CarState:
    __slots__ = ("id", "name", "x", "z", "vx", "vz", "angle", "hp", "score", "alive")

    def __init__(self, player_id: int, name: str, x: float, z: float, angle: float):
        self.id    = player_id
        self.name  = name
        self.x     = x
        self.z     = z
        self.vx    = 0.0
        self.vz    = 0.0
        self.angle = angle   # degrees; 0 = +Z axis, increases clockwise
        self.hp    = CAR_HP
        self.score = 0
        self.alive = True

    def to_dict(self) -> dict:
        return {
            "id":    self.id,
            "x":     round(self.x,     4),
            "z":     round(self.z,     4),
            "angle": round(self.angle, 2),
            "vx":    round(self.vx,    4),
            "vz":    round(self.vz,    4),
            "hp":    self.hp,
            "score": self.score,
            "alive": self.alive,
        }


class Simulation:
    def __init__(self, players: list[dict]):
        """
        players: list of {"id": int, "name": str}
        """
        self.tick    = 0
        self.elapsed = 0.0
        self.running = True
        self.winner_id: int | None = None

        self.cars:   dict[int, CarState]        = {}
        self._inputs: dict[int, tuple[float, float]] = {}  # id -> (forward, turn)

        self._spawn(players)

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _spawn(self, players: list[dict]):
        n = len(players)
        radius = min(ARENA_WIDTH, ARENA_DEPTH) * 0.28
        for i, p in enumerate(players):
            theta = math.radians(i * 360.0 / n) if n > 1 else 0.0
            x = radius * math.sin(theta)
            z = radius * math.cos(theta)
            facing = (math.degrees(theta) + 180.0) % 360.0  # face centre
            self.cars[p["id"]] = CarState(p["id"], p["name"], x, z, facing)
            self._inputs[p["id"]] = (0.0, 0.0)

    # ── Input ─────────────────────────────────────────────────────────────────

    def apply_input(self, player_id: int, forward: float, turn: float):
        """Store the latest input from a client; called by host on INPUT packet."""
        if player_id not in self.cars:
            return
        self._inputs[player_id] = (
            max(-1.0, min(1.0, forward)),
            max(-1.0, min(1.0, turn)),
        )

    # ── Main step ─────────────────────────────────────────────────────────────

    def step(self, dt: float):
        """Advance the simulation by dt seconds. No-op once the game is over."""
        if not self.running:
            return

        self.tick    += 1
        self.elapsed += dt

        for car in self.cars.values():
            if car.alive:
                self._move(car, dt)

        self._resolve_collisions()

        for car in self.cars.values():
            if car.alive:
                self._wall_bounce(car)

        self._check_win()

    # ── Movement ──────────────────────────────────────────────────────────────

    def _move(self, car: CarState, dt: float):
        forward, turn = self._inputs[car.id]

        # Rotate heading
        car.angle = (car.angle + turn * CAR_TURN_SPEED * dt) % 360.0

        # Thrust along heading vector
        rad = math.radians(car.angle)
        car.vx += forward * CAR_ACCELERATION * math.sin(rad) * dt
        car.vz += forward * CAR_ACCELERATION * math.cos(rad) * dt

        # Speed-independent friction (linear drag)
        speed = math.hypot(car.vx, car.vz)
        if speed > 0:
            new_speed = max(0.0, speed - CAR_FRICTION * dt)
            scale = new_speed / speed
            car.vx *= scale
            car.vz *= scale

        # Speed cap
        speed = math.hypot(car.vx, car.vz)
        if speed > CAR_MAX_SPEED:
            scale = CAR_MAX_SPEED / speed
            car.vx *= scale
            car.vz *= scale

        car.x += car.vx * dt
        car.z += car.vz * dt

    # ── Wall bounce ───────────────────────────────────────────────────────────

    def _wall_bounce(self, car: CarState):
        if car.x < -_HALF_W:
            car.x = -_HALF_W
            if car.vx < 0:
                car.vx = -car.vx * WALL_RESTITUTION
        elif car.x > _HALF_W:
            car.x = _HALF_W
            if car.vx > 0:
                car.vx = -car.vx * WALL_RESTITUTION

        if car.z < -_HALF_D:
            car.z = -_HALF_D
            if car.vz < 0:
                car.vz = -car.vz * WALL_RESTITUTION
        elif car.z > _HALF_D:
            car.z = _HALF_D
            if car.vz > 0:
                car.vz = -car.vz * WALL_RESTITUTION

    # ── Car-car collisions ────────────────────────────────────────────────────

    def _resolve_collisions(self):
        alive = [c for c in self.cars.values() if c.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                self._collide_pair(alive[i], alive[j])

    def _collide_pair(self, a: CarState, b: CarState):
        dx = b.x - a.x
        dz = b.z - a.z
        dist_sq = dx * dx + dz * dz
        min_dist = 2.0 * CAR_RADIUS

        if dist_sq >= min_dist * min_dist or dist_sq == 0.0:
            return

        dist = math.sqrt(dist_sq)
        nx = dx / dist   # unit normal a → b
        nz = dz / dist

        # Positional correction: push apart so they just touch
        overlap = (min_dist - dist) * 0.5
        a.x -= nx * overlap
        a.z -= nz * overlap
        b.x += nx * overlap
        b.z += nz * overlap

        # Relative approach speed along normal (pre-impulse)
        approach = (a.vx - b.vx) * nx + (a.vz - b.vz) * nz
        if approach <= 0:
            return  # already separating, no impulse needed

        # Determine attacker BEFORE applying impulse (uses pre-collision velocities)
        significant = approach >= _MIN_BUMP_SPEED
        if significant:
            a_toward_b = a.vx * nx + a.vz * nz      # how fast a moves into b
            b_toward_a = -(b.vx * nx + b.vz * nz)   # how fast b moves into a

        # Impulse (equal mass simplifies to halved approach)
        impulse = (1.0 + BUMP_RESTITUTION) * approach / 2.0
        a.vx -= impulse * nx
        a.vz -= impulse * nz
        b.vx += impulse * nx
        b.vz += impulse * nz

        # Scoring & damage
        if significant:
            if a_toward_b >= b_toward_a:
                a.score += 1
                b.hp -= 1
                if b.hp <= 0:
                    b.alive = False
            else:
                b.score += 1
                a.hp -= 1
                if a.hp <= 0:
                    a.alive = False

    # ── Win condition ─────────────────────────────────────────────────────────

    def _check_win(self):
        if SCORE_MODE == "last_standing":
            alive = [c for c in self.cars.values() if c.alive]
            # Only trigger last-standing when multiple players were present;
            # a solo player can drive freely without the game ending instantly.
            if len(alive) <= 1 and len(self.cars) > 1:
                self.running  = False
                self.winner_id = alive[0].id if alive else None
        else:  # "most_bumps"
            if self.elapsed >= GAME_DURATION:
                self.running = False
                best = max(self.cars.values(), key=lambda c: c.score, default=None)
                self.winner_id = best.id if best else None

    # ── State export ──────────────────────────────────────────────────────────

    def get_car_states(self) -> list[dict]:
        """Returns data for protocol.mk_game_state()."""
        return [c.to_dict() for c in self.cars.values()]

    def get_initial_positions(self) -> list[dict]:
        """Returns data for protocol.mk_game_start()."""
        return [{"id": c.id, "x": c.x, "z": c.z, "angle": c.angle}
                for c in self.cars.values()]

    def get_scores(self) -> list[dict]:
        """Returns ranked data for protocol.mk_game_over()."""
        ranked = sorted(self.cars.values(), key=lambda c: c.score, reverse=True)
        return [
            {"id": c.id, "name": c.name, "score": c.score, "winner": c.id == self.winner_id}
            for c in ranked
        ]


# ── Standalone smoke-test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    players = [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
        {"id": 3, "name": "Carol"},
    ]
    sim = Simulation(players)

    print("Initial positions:")
    for pos in sim.get_initial_positions():
        print(f"  {pos}")

    # Drive car 1 straight ahead, car 2 straight ahead (toward each other)
    sim.apply_input(1, forward=1.0, turn=0.0)
    sim.apply_input(2, forward=1.0, turn=0.0)
    sim.apply_input(3, forward=0.0, turn=0.0)

    dt = 1.0 / 30.0
    for _ in range(300):   # 10 simulated seconds
        sim.step(dt)
        if not sim.running:
            break

    print(f"\nAfter {sim.tick} ticks ({sim.elapsed:.1f}s):")
    for state in sim.get_car_states():
        print(f"  {state}")

    print(f"\nGame over: {not sim.running}  winner_id: {sim.winner_id}")
    print("\nScores:")
    for row in sim.get_scores():
        print(f"  {row}")
