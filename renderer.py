"""
Ursina 3D renderer for the Bumper Car Game — polished version.

Polish features
---------------
  - Hit flash: car briefly flashes white when taking damage
  - Camera shake: screen shakes when local player is hit
  - Elimination sink: car sinks through the floor when knocked out
  - Countdown overlay: 3-2-1-GO before the game begins
  - Score flash: scoreboard gold-flashes when you score a bump
  - Ambient + directional lighting
  - Development-mode UI disabled (no FPS counter / exit button)
"""
from __future__ import annotations

import math
import random
import time as _time

from ursina import (
    Ursina, Entity, Text, Vec3,
    color, held_keys, camera, window,
    destroy, application, time, lerp,
)
from ursina.lights import DirectionalLight, AmbientLight

from config import (
    ARENA_WIDTH, ARENA_DEPTH, WALL_THICKNESS,
    PLAYER_COLORS, CAR_HP, LOBBY_COUNTDOWN,
)

# ── Camera constants ──────────────────────────────────────────────────────────
_CAM_BACK   = 10.0
_CAM_HEIGHT =  7.0
_CAM_SPEED  =  8.0     # smoothing speed (higher = snappier)
_SHAKE_DUR  =  0.30    # seconds of shake after being hit
# Fixed downward tilt so the camera at (0, HEIGHT, -BACK) looks at the car below
_CAM_PITCH  = math.degrees(math.atan2(_CAM_HEIGHT, _CAM_BACK))  # ≈ 35°


# ── Per-car visuals ───────────────────────────────────────────────────────────

class _CarVisual:
    """All Ursina entities for one car in the scene."""

    def __init__(self, player_id: int, name: str):
        self._name      = name
        self._prev_hp   = CAR_HP
        self._prev_score = 0
        self._was_alive = True

        idx  = (player_id - 1) % len(PLAYER_COLORS)
        r, g, b = PLAYER_COLORS[idx]
        self._base  = color.rgb(int(r * 255), int(g * 255), int(b * 255))
        self._dark  = color.rgb(int(r * 140), int(g * 140), int(b * 140))
        self._roof  = lerp(self._base, color.white, 0.22)

        # Body (flattened box, sits on floor: y = height/2 = 0.25)
        self.body = Entity(
            model='cube',
            color=self._base,
            scale=(1.2, 0.5, 2.0),
            y=0.25,
        )
        # Cabin roof
        self.cab = Entity(
            parent=self.body,
            model='cube',
            color=self._roof,
            scale=(0.65, 0.85, 0.52),
            position=(0, 0.68, -0.1),
        )
        # Front bumper stripe (darker — shows which end is "front")
        self.bumper = Entity(
            parent=self.body,
            model='cube',
            color=self._dark,
            scale=(1.05, 0.55, 0.13),
            position=(0, 0.0, 0.565),
        )
        # Floating name + HP bar above the car
        self.label = Text(
            text=self._label_text(CAR_HP),
            world_space=True,
            scale=2,
            origin=(0, 0),
            color=color.white,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _label_text(self, hp: int) -> str:
        bar = '*' * max(0, hp) + '-' * max(0, CAR_HP - hp)
        return f'{self._name}\n{bar}'

    # ── State update ──────────────────────────────────────────────────────────

    def set_state(self, x: float, z: float, angle: float,
                  hp: int, score: int, alive: bool,
                  cam_heading: float = 0.0) -> dict:
        """
        Sync visuals to simulation state.
        Returns {'hit': bool, 'scored': bool} for triggering effects.
        """
        hit    = alive and hp    < self._prev_hp
        scored = alive and score > self._prev_score
        self._prev_hp    = hp
        self._prev_score = score

        if self._was_alive and not alive:
            # ── Elimination: sink below the floor ─────────────────────────
            self.body.animate_y(-1.5, duration=0.55)
            self.label.visible = False
            self._was_alive = False

        elif alive:
            # ── Normal update ─────────────────────────────────────────────
            self.body.x          = x
            self.body.z          = z
            self.body.rotation_y = angle

            if hit:
                # Flash white then fade back to base colour
                self.body.color = color.white
                self.body.animate_color(self._base, duration=0.25)
                self.cab.color  = color.white
                self.cab.animate_color(self._roof, duration=0.25)

            self.label.visible        = True
            self.label.world_position = Vec3(x, 1.9, z)
            self.label.rotation_y     = cam_heading + 180  # face toward camera
            self.label.text           = self._label_text(hp)

        return {'hit': hit, 'scored': scored}

    def remove(self):
        destroy(self.body)   # destroys parented children too
        destroy(self.label)


# ── Ursina update / input hook ────────────────────────────────────────────────

class _GameLoop(Entity):
    """Entity subclass; Ursina calls update() and input() automatically."""

    def __init__(self, renderer: GameRenderer):
        super().__init__()
        self._r = renderer

    def update(self):
        self._r.frame_update()

    def input(self, key: str):
        self._r.on_key(key)


# ── Main renderer ─────────────────────────────────────────────────────────────

class GameRenderer:
    """
    Accepts a Host or Client instance (duck-typed).
    Call run() from the main thread — it blocks until the window closes.
    """

    def __init__(self, player_obj, is_host: bool = False):
        self._player  = player_obj
        self._is_host = is_host

        self._car_visuals: dict[int, _CarVisual] = {}
        self._cam_angle = 0.0   # degrees; smoothed toward car's heading
        self._cam_pivot: Entity | None = None
        self._shake_t   = 0.0   # seconds of shake remaining

        self._game_start_time: float | None = None
        self._prev_phase = ''

        # HUD text objects (created in run())
        self._phase_text:      Text | None = None
        self._scoreboard:      Text | None = None
        self._sb_bg:           Entity | None = None
        self._controls_hint:   Text | None = None
        self._countdown_text:  Text | None = None

        self._app = None

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        """Start Ursina — blocks until the window closes."""
        self._app = Ursina(title='Bumper Car Arena', borderless=False)

        # Background colour (dark navy)
        self._app.setBackgroundColor(15/255, 20/255, 35/255, 1)

        # Strip development-mode clutter
        application.development_mode = False
        for attr in ('exit_button', 'fps_counter', 'cog_menu'):
            try:
                getattr(window, attr).visible = False
            except Exception:
                pass

        self._build_arena()
        self._add_lighting()
        self._build_hud()
        self._setup_camera()

        _GameLoop(self)
        self._app.run()

    # ── Scene construction ────────────────────────────────────────────────────

    def _build_arena(self):
        hw = ARENA_WIDTH  / 2
        hd = ARENA_DEPTH  / 2
        wh = 1.5                        # wall height
        t  = max(WALL_THICKNESS, 0.5)

        # Floor
        Entity(
            model='plane',
            scale=(ARENA_WIDTH, 1, ARENA_DEPTH),
            color=color.rgb(40, 70, 40),
        )

        # Grid lines (every 2 m) — help perceive depth and speed
        line_col = color.rgb(50, 87, 50)
        for xi in range(-int(hw) + 1, int(hw)):
            if xi % 2 == 0:
                Entity(model='cube', color=line_col,
                       scale=(0.04, 0.01, ARENA_DEPTH), x=xi, y=0.005)
        for zi in range(-int(hd) + 1, int(hd)):
            if zi % 2 == 0:
                Entity(model='cube', color=line_col,
                       scale=(ARENA_WIDTH, 0.01, 0.04), z=zi, y=0.005)

        # Boundary stripe on floor (bright edge, helps judge distance to wall)
        stripe = color.rgb(180, 60, 60)
        stripe_w = 0.25
        for pos, scale in [
            (Vec3(0, 0.003,  hd - stripe_w/2), (ARENA_WIDTH, 0.01, stripe_w)),
            (Vec3(0, 0.003, -hd + stripe_w/2), (ARENA_WIDTH, 0.01, stripe_w)),
            (Vec3( hw - stripe_w/2, 0.003, 0), (stripe_w, 0.01, ARENA_DEPTH)),
            (Vec3(-hw + stripe_w/2, 0.003, 0), (stripe_w, 0.01, ARENA_DEPTH)),
        ]:
            Entity(model='cube', position=pos, scale=scale, color=stripe)

        # Walls
        wall_col = color.rgb(85, 90, 115)
        for pos, scale in [
            (Vec3(0,      wh/2,  hd+t/2), (ARENA_WIDTH+2*t, wh, t)),
            (Vec3(0,      wh/2, -hd-t/2), (ARENA_WIDTH+2*t, wh, t)),
            (Vec3( hw+t/2, wh/2, 0),      (t, wh, ARENA_DEPTH)),
            (Vec3(-hw-t/2, wh/2, 0),      (t, wh, ARENA_DEPTH)),
        ]:
            Entity(model='cube', position=pos, scale=scale, color=wall_col)

        # Corner pillars
        pillar_col = color.rgb(65, 70, 95)
        for sx, sz in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            Entity(
                model='cube',
                position=Vec3(sx * (hw + t/2), wh/2, sz * (hd + t/2)),
                scale=(t, wh + 0.3, t),
                color=pillar_col,
            )

    def _add_lighting(self):
        sun = DirectionalLight()
        sun.look_at(Vec3(1, -2, 1))
        AmbientLight(color=color.rgba(80, 80, 100, 255))

    def _setup_camera(self):
        # Pivot sits at the car's world position and rotates only around Y.
        # Camera is a child with a fixed local offset + fixed downward tilt.
        # This makes roll structurally impossible — no look_at ever needed.
        self._cam_pivot = Entity()
        camera.parent   = self._cam_pivot
        camera.position = Vec3(0, _CAM_HEIGHT, -_CAM_BACK)
        camera.rotation = Vec3(_CAM_PITCH, 0, 0)
        camera.fov      = 110

    def _build_hud(self):
        self._phase_text = Text(
            text='',
            position=(0, 0.47),
            origin=(0, 0.5),   # top-anchored so text flows downward, never clips
            scale=1.8,
            color=color.white,
        )
        self._scoreboard = Text(
            text='',
            position=(0.88, 0.45),
            origin=(1, 0.5),
            scale=1.5,
            color=color.white,
        )
        self._sb_bg = None  # removed — white text is readable on dark background
        self._controls_hint = Text(
            text='',
            position=(0, -0.47),
            origin=(0, 0),
            scale=1.2,
            color=color.rgb(150, 150, 155),
        )
        # Large countdown overlay (hidden when not in use)
        self._countdown_text = Text(
            text='',
            position=(0, 0.05),
            origin=(0, 0),
            scale=7,
            color=color.yellow,
        )

    # ── Per-frame loop ────────────────────────────────────────────────────────

    def frame_update(self):
        phase = self._player.game_phase

        if phase == 'game' and self._prev_phase != 'game':
            # Just entered game — record time and reset camera
            self._game_start_time = _time.time()
            self._cam_angle = 0.0

        self._prev_phase = phase

        if phase in ('discovering', 'joining'):
            self._draw_connecting(phase)
        elif phase == 'lobby':
            self._draw_lobby()
        elif phase == 'game':
            self._draw_game()
        elif phase == 'over':
            self._draw_over()

    def on_key(self, key: str):
        if key == 'escape':
            application.quit()
        if key == 'enter' and self._is_host and self._player.game_phase == 'lobby':
            self._player.start_game()

    # ── Phase drawing ─────────────────────────────────────────────────────────

    def _draw_connecting(self, phase: str):
        if phase == 'discovering':
            self._phase_text.text = 'Searching for host on LAN...'
        else:
            host = getattr(self._player, 'host_name', None) or '?'
            self._phase_text.text = f'Connecting to  {host}...'
        self._clear_game_hud()
        self._controls_hint.text = 'ESC: quit'

    def _draw_lobby(self):
        roster = self._player.roster
        lines  = [f'--- LOBBY  ({len(roster)} players) ---\n']
        for p in roster:
            lines.append(f'  {p["name"]}')
        lines.append('')
        lines.append('[Enter]  Start Game' if self._is_host
                     else 'Waiting for host to start...')

        self._phase_text.text    = '\n'.join(lines)
        self._clear_game_hud()
        self._controls_hint.text = 'ESC: quit'

    def _draw_game(self):
        elapsed      = _time.time() - (self._game_start_time or _time.time())
        in_countdown = elapsed < LOBBY_COUNTDOWN

        # ── Countdown overlay ─────────────────────────────────────────────
        if in_countdown:
            remaining = int(LOBBY_COUNTDOWN - elapsed) + 1
            self._countdown_text.text  = str(remaining)
            self._countdown_text.color = color.yellow
            self._phase_text.text      = ''
            self._controls_hint.text   = ''
        elif elapsed < LOBBY_COUNTDOWN + 0.6:
            self._countdown_text.text  = 'GO!'
            self._countdown_text.color = color.lime
        else:
            self._countdown_text.text = ''

        # ── Input (suppressed during countdown) ───────────────────────────
        if in_countdown:
            self._push_input(0.0, 0.0)
        else:
            fwd  = float(
                (held_keys['w'] or held_keys['up arrow']) -
                (held_keys['s'] or held_keys['down arrow'])
            )
            turn = float(
                (held_keys['d'] or held_keys['right arrow']) -
                (held_keys['a'] or held_keys['left arrow'])
            )
            self._push_input(fwd, turn)

        # ── Car visuals ───────────────────────────────────────────────────
        cars = self._player.get_sim_state()
        if not cars:
            return

        my_id = self._my_id()

        for car in cars:
            cid = car['id']
            if cid not in self._car_visuals:
                self._car_visuals[cid] = _CarVisual(cid, self._name_for(cid))

            result = self._car_visuals[cid].set_state(
                car['x'], car['z'], car['angle'],
                car['hp'], car['score'], car['alive'],
                self._cam_angle,
            )
            if result['hit'] and cid == my_id:
                self._shake_t = _SHAKE_DUR      # trigger camera shake
            if result['scored'] and cid == my_id:
                # Gold-flash the scoreboard when we score
                try:
                    self._scoreboard.color = color.gold
                    self._scoreboard.animate_color(color.white, duration=0.5)
                except Exception:
                    pass

        # ── Camera ────────────────────────────────────────────────────────
        my_car = next((c for c in cars if c['id'] == my_id), None)
        if my_car:
            self._follow_camera(my_car)

        # ── Scoreboard ────────────────────────────────────────────────────
        if not in_countdown:
            by_score = sorted(cars, key=lambda c: c['score'], reverse=True)
            sb_lines = []
            for car in by_score:
                name   = self._name_for(car['id'])
                tag    = '>>' if car['id'] == my_id else '  '
                hp_bar = '*' * max(0, car['hp']) + '-' * max(0, CAR_HP - car['hp'])
                out    = '  x' if not car['alive'] else ''
                sb_lines.append(
                    f"{tag} {name}  {hp_bar}  #{car['score']}{out}"
                )
            self._scoreboard.text   = '\n'.join(sb_lines)
            self._phase_text.text   = ''
            self._controls_hint.text = 'WASD / Arrows: drive     ESC: quit'

    def _draw_over(self):
        scores = self._player.final_scores
        medals = ['  1st', '  2nd', '  3rd', '  4th', '  5th', '  6th']
        lines  = ['== GAME OVER ==\n']

        winner = next((r for r in scores if r.get('winner')), None)
        if winner:
            lines.append(f'Winner:  {winner["name"]} !\n')

        for i, row in enumerate(scores):
            m = medals[i] if i < len(medals) else '      '
            lines.append(f'{m}  {row["name"]:14s}  {row["score"]} bumps')

        lines += ['', 'ESC: quit']
        self._phase_text.text    = '\n'.join(lines)
        self._clear_game_hud()
        self._controls_hint.text = ''

    # ── Camera (smooth follow + shake) ────────────────────────────────────────

    def _follow_camera(self, car: dict):
        # Smoothly rotate the pivot toward the car's heading (shortest path).
        target = car['angle']
        diff = ((target - self._cam_angle + 180) % 360) - 180
        self._cam_angle = (self._cam_angle + diff * min(1.0, time.dt * _CAM_SPEED)) % 360

        # Pivot moves to car's ground position and spins in Y only.
        # Camera's local transform is fixed: behind + above + tilted down.
        self._cam_pivot.position  = Vec3(car['x'], 0, car['z'])
        self._cam_pivot.rotation_y = self._cam_angle

        # Shake: jitter the camera's local position, then snap back.
        if self._shake_t > 0:
            self._shake_t = max(0.0, self._shake_t - time.dt)
            mag = self._shake_intensity()
            camera.position = Vec3(
                random.uniform(-1, 1) * mag,
                _CAM_HEIGHT + random.uniform(-0.4, 0.4) * mag,
                -_CAM_BACK  + random.uniform(-1, 1) * mag,
            )
        else:
            camera.position = Vec3(0, _CAM_HEIGHT, -_CAM_BACK)

    def _shake_intensity(self) -> float:
        return 0.35 * (self._shake_t / _SHAKE_DUR)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _clear_game_hud(self):
        self._scoreboard.text     = ''
        self._countdown_text.text = ''

    def _my_id(self) -> int:
        if self._is_host:
            return self._player.my_player_id
        return self._player.player_id or 0

    def _push_input(self, forward: float, turn: float):
        if self._is_host:
            self._player.apply_local_input(forward, turn)
        else:
            self._player.set_input(forward, turn)

    def _name_for(self, player_id: int) -> str:
        return next(
            (p['name'] for p in self._player.roster if p['id'] == player_id),
            f'P{player_id}',
        )
