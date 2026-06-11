"""raylib compatibility layer — works in both native (raylib/pyray) and web (Pyodide/Canvas2D).

Native mode: imports from raylib or pyray C bindings and re-exports with
snake_case wrappers and UTF-8 encoding helpers.

Web mode: collects draw commands into a flat array.array('d') buffer.
At end_drawing(), the buffer is sent to JavaScript via a single zero-copy
to_js() call, where renderer.js executes Canvas2D commands natively.
"""
from __future__ import annotations

import sys

_WEB = sys.platform == "emscripten"

# ══════════════════════════════════════════════════════════════════════
# Native mode — import from raylib/pyray C bindings
# ══════════════════════════════════════════════════════════════════════

if not _WEB:
    try:
        from raylib import *  # type: ignore
    except Exception:
        try:
            from pyray import *  # type: ignore
        except Exception as exc:
            raise ImportError(
                "Could not import raylib bindings. Install 'raylib' or 'pyray'."
            ) from exc

    # Some bindings expose Color/Vector2 as structs, others use plain tuples.
    if "Color" not in globals():
        def Color(r: int, g: int, b: int, a: int):  # type: ignore
            return (r, g, b, a)

    if "Vector2" not in globals():
        def Vector2(x: float, y: float):  # type: ignore
            return (x, y)

    if "Rectangle" not in globals():
        def Rectangle(x: float, y: float, width: float, height: float):  # type: ignore
            return (x, y, width, height)

    if "Texture2D" not in globals():
        class Texture2D:  # type: ignore
            pass

    # Map common snake_case names to CamelCase raylib bindings if needed.
    _CAMEL_MAP = {
        "init_window": "InitWindow",
        "set_target_fps": "SetTargetFPS",
        "window_should_close": "WindowShouldClose",
        "begin_drawing": "BeginDrawing",
        "clear_background": "ClearBackground",
        "end_drawing": "EndDrawing",
        "get_frame_time": "GetFrameTime",
        "is_key_pressed": "IsKeyPressed",
        "is_key_down": "isKeyDown",
        "load_texture": "LoadTexture",
        "unload_texture": "UnloadTexture",
        "draw_text": "DrawText",
        "draw_rectangle": "DrawRectangle",
        "draw_rectangle_lines": "DrawRectangleLines",
        "draw_texture_ex": "DrawTextureEx",
        "draw_texture_pro": "DrawTexturePro",
        "close_window": "CloseWindow",
        "get_mouse_position": "GetMousePosition",
        "is_mouse_button_pressed": "IsMouseButtonPressed",
        "is_mouse_button_down": "IsMouseButtonDown",
        "is_mouse_button_released": "isMouseButtonReleased",
        "measure_text": "MeasureText",
        "get_time": "GetTime",
        "take_screenshot": "TakeScreenshot",
        "set_exit_key": "SetExitKey",
        "begin_scissor_mode": "BeginScissorMode",
        "end_scissor_mode": "EndScissorMode",
        "get_mouse_wheel_move": "GetMouseWheelMove",
        "is_mouse_button_released": "IsMouseButtonReleased",
    }

    for _snake, _camel in _CAMEL_MAP.items():
        if _snake not in globals() and _camel in globals():
            globals()[_snake] = globals()[_camel]

    if "MOUSE_BUTTON_MIDDLE" not in globals():
        MOUSE_BUTTON_MIDDLE = 2  # type: ignore

    def _encode_text(value):  # type: ignore
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    # Wrap common functions that expect const char*
    if "init_window" in globals():
        _init_window = globals()["init_window"]
        def init_window(width, height, title):  # type: ignore
            return _init_window(width, height, _encode_text(title))
        globals()["init_window"] = init_window

    if "draw_text" in globals():
        _draw_text = globals()["draw_text"]
        def draw_text(text, x, y, size, color):  # type: ignore
            return _draw_text(_encode_text(text), x, y, size, color)
        globals()["draw_text"] = draw_text

    if "measure_text" in globals():
        _measure_text = globals()["measure_text"]
        def measure_text(text, size):  # type: ignore
            return _measure_text(_encode_text(text), size)
        globals()["measure_text"] = measure_text
    else:
        def measure_text(text, size):  # type: ignore
            return None
        globals()["measure_text"] = measure_text

    if "take_screenshot" in globals():
        _take_screenshot = globals()["take_screenshot"]
        def take_screenshot(path):  # type: ignore
            return _take_screenshot(_encode_text(path))
        globals()["take_screenshot"] = take_screenshot

    if "load_texture" in globals():
        _load_texture = globals()["load_texture"]
        def load_texture(path):  # type: ignore
            return _load_texture(_encode_text(path))
        globals()["load_texture"] = load_texture

    def get_pending_file_import():
        """No-op on native (file import uses tkinter dialog)."""
        return None


# ══════════════════════════════════════════════════════════════════════
# Web mode — Canvas2D batch command system
# ══════════════════════════════════════════════════════════════════════

else:
    import array
    import time as _time
    from pyodide.ffi import to_js as _to_js  # type: ignore

    # ── Opcodes ───────────────────────────────────────────────────────
    OP_CLEAR_BG = 0
    OP_FILL_RECT = 1
    OP_STROKE_RECT = 2
    OP_DRAW_TEXT = 3
    OP_TEXTURE_PRO = 4
    OP_TEXTURE_EX = 5
    OP_BEGIN_SCISSOR = 6
    OP_END_SCISSOR = 7
    OP_DRAW_TEXT_LIGHT = 8

    # ── Command buffer (module-level, reused across frames) ───────────
    # Use array.array('d') directly so to_js can use the buffer protocol
    # without an intermediate copy.  extend() with tuples is efficient.
    _cmds: array.array = array.array('d')
    _strings: list[str] = []

    # ── Input state (polled once per frame from JS) ──────────────────
    _input_state: dict = {}
    _prev_input_state: dict = {}
    _frame_time: float = 1.0 / 60.0
    _start_time: float = _time.time()

    # ── JS bridge (set by loader.js after Pyodide boots) ─────────────
    _js_render_batch = None
    _js_measure_text = None
    _js_get_texture_info = None
    _js_poll_input = None

    def _set_js_bridge(render_batch, measure_text_fn, get_texture_info, poll_input):
        """Called from loader.js to provide JS function references."""
        global _js_render_batch, _js_measure_text, _js_get_texture_info, _js_poll_input
        _js_render_batch = render_batch
        _js_measure_text = measure_text_fn
        _js_get_texture_info = get_texture_info
        _js_poll_input = poll_input

    # ── Type surrogates ───────────────────────────────────────────────

    def Color(r: int, g: int, b: int, a: int) -> tuple:
        return (r, g, b, a)

    def Vector2(x: float, y: float) -> tuple:
        return (x, y)

    def Rectangle(x: float, y: float, width: float, height: float) -> tuple:
        return (x, y, width, height)

    class Texture2D:
        """Lightweight texture handle.  JS manages actual Image objects."""
        __slots__ = ('id', 'width', 'height', '_name')
        def __init__(self, tex_id: int = 0, width: int = 0, height: int = 0, name: str = ''):
            self.id = tex_id
            self.width = width
            self.height = height
            self._name = name

    class _Pos:
        """Reusable mouse position container."""
        __slots__ = ('x', 'y')
        def __init__(self, x, y):
            self.x = x; self.y = y

    # ── Key / mouse constants ────────────────────────────────────────
    KEY_F1 = 112
    KEY_F2 = 113
    KEY_F3 = 114
    KEY_F4 = 115
    KEY_F5 = 116
    KEY_SHIFT = 16
    KEY_SPACE = 32
    KEY_ESCAPE = 27
    KEY_X = 88
    KEY_Y = 89

    MOUSE_BUTTON_LEFT = 0
    MOUSE_BUTTON_RIGHT = 2
    MOUSE_BUTTON_MIDDLE = 1

    # ── Window management (no-ops) ────────────────────────────────────

    def init_window(width: int, height: int, title: str) -> None:
        pass

    def close_window() -> None:
        pass

    def set_target_fps(fps: int) -> None:
        pass

    def set_exit_key(key: int) -> None:
        pass

    def WindowShouldClose() -> bool:
        return False

    # ── Frame timing ─────────────────────────────────────────────────

    def get_frame_time() -> float:
        return _frame_time

    def get_time() -> float:
        return _time.time() - _start_time

    # ── Drawing frame ────────────────────────────────────────────────

    # Reusable mouse position — avoids allocating a new object every frame
    _mouse_pos = _Pos(0, 0)

    def begin_drawing() -> None:
        global _prev_input_state, _input_state, _frame_time
        del _cmds[:]  # Clear in-place; array keeps its allocated buffer
        _strings.clear()
        _prev_input_state = _input_state
        if _js_poll_input is not None:
            raw = _js_poll_input()
            if hasattr(raw, 'to_py'):
                _input_state = raw.to_py()
                if hasattr(raw, 'destroy'):
                    raw.destroy()
            else:
                _input_state = dict(raw)
            _frame_time = _input_state.get('dt', 1.0 / 60.0)
        else:
            _input_state = {}
            _frame_time = 1.0 / 60.0

    def end_drawing() -> None:
        if _js_render_batch is not None:
            addr, count = _cmds.buffer_info()
            js_strings = _to_js(_strings)
            _js_render_batch(addr, count, js_strings)
            if hasattr(js_strings, 'destroy'):
                js_strings.destroy()

    # ── Background ───────────────────────────────────────────────────

    def clear_background(color: tuple) -> None:
        _cmds.extend((OP_CLEAR_BG, color[0], color[1], color[2], color[3]))

    # ── Rectangle drawing ────────────────────────────────────────────

    def draw_rectangle(x: int, y: int, width: int, height: int, color: tuple) -> None:
        _cmds.extend((OP_FILL_RECT, float(x), float(y), float(width), float(height),
                       color[0], color[1], color[2], color[3]))

    def draw_rectangle_lines(x: int, y: int, width: int, height: int, color: tuple) -> None:
        _cmds.extend((OP_STROKE_RECT, float(x), float(y), float(width), float(height),
                       color[0], color[1], color[2], color[3]))

    # ── Text ─────────────────────────────────────────────────────────

    def draw_text(text: str, x: int, y: int, size: int, color: tuple, light=False) -> None:
        str_idx = len(_strings)
        _strings.append(str(text))
        opcode = OP_DRAW_TEXT_LIGHT if light else OP_DRAW_TEXT
        _cmds.extend((opcode, float(str_idx), float(x), float(y), float(size),
                       color[0], color[1], color[2], color[3]))

    _measure_cache: dict[tuple, int] = {}

    def measure_text(text: str, size: int) -> int:
        key = (text, size)
        cached = _measure_cache.get(key)
        if cached is not None:
            return cached
        if _js_measure_text is not None:
            result = int(_js_measure_text(str(text), size))
        else:
            result = int(len(str(text)) * size * 0.6)
        _measure_cache[key] = result
        return result

    # ── Textures ─────────────────────────────────────────────────────

    _texture_cache: dict[str, Texture2D] = {}

    def load_texture(path: str) -> Texture2D:
        import os
        name = os.path.basename(str(path))
        if name in _texture_cache:
            return _texture_cache[name]
        tid, w, h = 0, 32, 32
        if _js_get_texture_info is not None:
            info = _js_get_texture_info(name)
            if info is not None:
                if hasattr(info, 'to_py'):
                    info_py = info.to_py()
                    if hasattr(info, 'destroy'):
                        info.destroy()
                else:
                    info_py = dict(info)
                tid = int(info_py.get('id', 0))
                w = int(info_py.get('width', 32))
                h = int(info_py.get('height', 32))
        tex = Texture2D(tid, w, h, name)
        _texture_cache[name] = tex
        return tex

    def unload_texture(texture) -> None:
        pass

    def draw_texture_pro(texture, src_rect: tuple, dst_rect: tuple,
                         origin: tuple, rotation: float, tint: tuple) -> None:
        _cmds.extend((OP_TEXTURE_PRO, float(texture.id),
                       float(src_rect[0]), float(src_rect[1]),
                       float(src_rect[2]), float(src_rect[3]),
                       float(dst_rect[0]), float(dst_rect[1]),
                       float(dst_rect[2]), float(dst_rect[3]),
                       tint[0], tint[1], tint[2], tint[3]))

    def draw_texture_ex(texture, position: tuple, rotation: float,
                        scale: float, tint: tuple) -> None:
        _cmds.extend((OP_TEXTURE_EX, float(texture.id),
                       float(position[0]), float(position[1]),
                       float(rotation), float(scale),
                       tint[0], tint[1], tint[2], tint[3]))

    # ── Scissor mode ─────────────────────────────────────────────────

    def begin_scissor_mode(x: int, y: int, w: int, h: int) -> None:
        _cmds.extend((OP_BEGIN_SCISSOR, float(x), float(y), float(w), float(h)))

    def end_scissor_mode() -> None:
        _cmds.append(OP_END_SCISSOR)

    # ── Input ────────────────────────────────────────────────────────

    def get_mouse_position():
        _mouse_pos.x = _input_state.get('mouseX', 0)
        _mouse_pos.y = _input_state.get('mouseY', 0)
        return _mouse_pos

    def is_mouse_button_pressed(button: int) -> bool:
        return button in _input_state.get('mousePressed', [])

    def is_mouse_button_down(button: int) -> bool:
        return button in _input_state.get('mouseDown', [])

    def is_mouse_button_released(button: int) -> bool:
        return button in _input_state.get('mouseReleased', [])

    def get_mouse_wheel_move() -> float:
        return _input_state.get('wheelDelta', 0.0)

    def is_key_pressed(key: int) -> bool:
        return key in _input_state.get('keysPressed', [])
    
    def is_key_down(key: int) -> bool:
        return key in _input_state.get('keysDown', [])

    # ── Utilities ────────────────────────────────────────────────────

    def take_screenshot(path: str) -> None:
        pass

    def get_pending_file_import():
        """Return file content queued by the JS file input, or None."""
        if 'fileImport' not in _input_state:
            return None
        val = _input_state['fileImport']
        return str(val) if val is not None else None
