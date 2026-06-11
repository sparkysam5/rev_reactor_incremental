from __future__ import annotations

import asyncio
import math
import sys

_WEB = sys.platform == "emscripten"

if _WEB:
    from js import Renderer as _Renderer  # type: ignore
    _wait_frame = _Renderer.waitFrame  # Returns a JS Promise; Pyodide awaits it natively

if not _WEB:
    from pathlib import Path

from raylib_compat import (
    Color,
    Texture2D,
    begin_drawing,
    begin_scissor_mode,
    clear_background,
    draw_rectangle,
    draw_text,
    draw_texture_pro,
    end_drawing,
    end_scissor_mode,
    get_frame_time,
    get_mouse_position,
    get_mouse_wheel_move,
    get_time,
    is_key_pressed,
    is_key_down,
    init_window,
    is_mouse_button_down,
    is_mouse_button_pressed,
    is_mouse_button_released,
    load_texture,
    measure_text,
    MOUSE_BUTTON_LEFT,
    MOUSE_BUTTON_MIDDLE,
    MOUSE_BUTTON_RIGHT,
    KEY_F1,
    KEY_F2,
    KEY_F3,
    KEY_SHIFT,
    KEY_SPACE,
    KEY_ESCAPE,
    Rectangle,
    Vector2,
    set_exit_key,
    set_target_fps,
    get_pending_file_import,
)

if not _WEB:
    from raylib_compat import (
        WindowShouldClose,
        close_window,
        KEY_F4,
        KEY_X,
        KEY_Y,
        take_screenshot,
        unload_texture,
    )

from game.simulation import Simulation
from game.save import save_game, load_game, _handle_file_import

from assets import sprite_path
if not _WEB:
    from assets import reference_path
from game.layout import load_layout
from game.grid import Grid
from game.simulation import ReactorComponent, ExplosionEffect, demo_simulation
from game.ui import Ui

# Global reference for beforeunload auto-save from JS
_sim_ref = None


def _load_texture(name: str) -> Texture2D:
    return load_texture(str(sprite_path(name)))


async def main() -> None:
    layout = load_layout()
    init_window(layout.window_width, layout.window_height, "Rev Reactor (Prototype)")
    set_exit_key(0)  # Disable raylib's default ESC-to-close
    set_target_fps(60)

    global _sim_ref

    sim = demo_simulation()
    _sim_ref = sim

    if _WEB:
        save_path = None  # Web port uses localStorage, not file path
    else:
        save_path = Path(__file__).resolve().parent / "save.json"

    heat_icon = _load_texture("HeatIcon.png")
    heat_tex = _load_texture("Heat.png")
    power_tex = _load_texture("Power.png")
    power_icon = _load_texture("PowerIcon.png")
    grid_tex = _load_texture("GridTile.png")
    grid_full = _load_texture("MainGrid.png")
    grid_backer = _load_texture("GridBacker.png")
    grid_frame = _load_texture("GridFrame.png")
    side_grid = _load_texture("SideGrid.png")
    main_upgrades = _load_texture("MainUpgrades.png")
    btn_big = _load_texture("ButtonBIG.png")
    btn_big_hover = _load_texture("ButtonBIGHover.png")
    btn_big_pressed = _load_texture("ButtonBIGClicked.png")
    btn_small = _load_texture("ButtonSMALL.png")
    btn_small_hover = _load_texture("ButtonSMALLHover.png")
    btn_small_pressed = _load_texture("ButtonSMALLClicked.png")
    btn_med = _load_texture("ButtonMED.png")
    btn_med_hover = _load_texture("ButtonMEDHover.png")
    btn_med_pressed = _load_texture("ButtonMEDClicked.png")
    btn_back = _load_texture("ButtonBACK.png")
    btn_back_hover = _load_texture("ButtonBACKHover.png")
    btn_back_pressed = _load_texture("ButtonBACKClicked.png")
    btn_play = _load_texture("ButtonPLAY.png")
    btn_play_hover = _load_texture("ButtonPLAYHover.png")
    btn_play_pressed = _load_texture("ButtonPLAYClicked.png")
    btn_pause = _load_texture("ButtonPAUSE.png")
    btn_pause_hover = _load_texture("ButtonPAUSEHover.png")
    btn_pause_pressed = _load_texture("ButtonPAUSEClicked.png")
    btn_replace = _load_texture("ButtonREPLACE.png")
    btn_replace_hover = _load_texture("ButtonREPLACEHover.png")
    btn_replace_pressed = _load_texture("ButtonREPLACEClicked.png")
    btn_noreplace = _load_texture("ButtonNOREPLACE.png")
    btn_noreplace_hover = _load_texture("ButtonNOREPLACEHover.png")
    btn_noreplace_pressed = _load_texture("ButtonNOREPLACEClicked.png")
    top_banner = _load_texture("TopBanner.png")
    store_block = _load_texture("Block.png")
    tab_power = _load_texture("Power.png")
    tab_power_hover = _load_texture("PowerHover.png")
    tab_power_pressed = _load_texture("PowerClicked.png")
    tab_heat = _load_texture("Heat.png")
    tab_heat_hover = _load_texture("HeatHover.png")
    tab_heat_pressed = _load_texture("HeatClicked.png")
    tab_experimental = _load_texture("Experimental.png")
    tab_experimental_hover = _load_texture("ExperimentalHover.png")
    tab_experimental_pressed = _load_texture("ExperimentalClicked.png")
    tab_arcane = _load_texture("Arcane.png")
    tab_arcane_hover = _load_texture("ArcaneHover.png")
    tab_arcane_pressed = _load_texture("ArcaneClicked.png")
    icon_btn = _load_texture("IconButton.png")
    icon_btn_hover = _load_texture("IconHoverButton.png")
    icon_btn_pressed = _load_texture("IconClickedButton.png")
    icon_btn_locked = _load_texture("IconButtonLocked.png")

    if _WEB:
        reference_textures = []
    else:
        reference_names = [
            "screenshot-main.png",
            "screenshot-upgrades.png",
            "screenshot-statistics.png",
            "screenshot-prestige.png",
            "screenshot-help.png",
            "screenshot-options.png",
        ]
        reference_textures = [(load_texture(str(reference_path(name))), name) for name in reference_names]

    # Explosion animation: 12 frames (RE: Explosion MonoBehaviour)
    explosion_textures = [_load_texture(f"Explosion_{i}.png") for i in range(12)]

    if sim.grid is None or sim.grid.base_width != layout.grid_width or sim.grid.base_height != layout.grid_height:
        sim.grid = Grid(
            width=layout.grid_width,
            height=layout.grid_height,
            tile_size=32,
            origin_x=layout.grid_origin_x,
            origin_y=layout.grid_origin_y,
            cell_size=layout.grid_cell_size,
            tile_texture=grid_tex,
            base_width=layout.grid_width,
            base_height=layout.grid_height,
        )
    grid = sim.grid
    grid.tile_texture = grid_tex
    grid.full_texture = grid_full

    component_sprites = {}
    for comp in sim.shop_components:
        if comp.sprite_name in component_sprites:
            continue
        try:
            component_sprites[comp.sprite_name] = _load_texture(comp.sprite_name)
        except Exception:
            continue

    # Load upgrade icon sprites: map upgrade icon/category paths to component sprites
    _UPGRADE_ICON_MAP = {
        "UI/Upgrade Icons/Fuel1": "Fuel1-1.png",
        "UI/Upgrade Icons/Fuel2": "Fuel2-1.png",
        "UI/Upgrade Icons/Fuel3": "Fuel3-1.png",
        "UI/Upgrade Icons/Fuel4": "Fuel4-1.png",
        "UI/Upgrade Icons/Fuel5": "Fuel5-1.png",
        "UI/Upgrade Icons/Fuel6": "Fuel6-1.png",
        "UI/Upgrade Icons/Vent": "Vent1.png",
        "UI/Upgrade Icons/Exchanger": "Exchanger1.png",
        "UI/Upgrade Icons/Coolant": "Coolant1.png",
        "UI/Upgrade Icons/Reflector": "Reflector1.png",
        "UI/Upgrade Icons/Wiring": "Capacitor1.png",
        "UI/Upgrade Icons/Alloys": "Plate1UP.png",
        "UI/Upgrade Icons/Heatsink": "Plate1UP.png",
        "UI/Upgrade Icons/PowerLines": "Capacitor1.png",
        "UI/Upgrade Icons/Piping": "Plate1.png",
        "UI/Upgrade Icons/Chronometer": "Clock.png",
        "UI/Upgrade Icons/Bartering": "Explosion_0.png",
        "UI/Upgrade Icons/Discount": "GridTile.png",
        "UI/Upgrade Icons/Research": "Fuel7-1.png",
        "UI/Upgrade Icons/ActiveVenting": "Capacitor1.png",
        "UI/Upgrade Icons/ActiveExchanger": "Capacitor1.png",
        "UI/Upgrade Icons/ReinforcedExchanger": "Plate1.png",
        "UI/Upgrade Icons/InfusedFuel": "Fuel5-4.png",
        "UI/Upgrade Icons/UnleashedFuel": "Fuel6-4.png",
        "UI/Upgrade Icons/QuantumBuffering": "Capacitor5.png",
        "UI/Upgrade Icons/FullSpectrum": "Reflector5.png",
        "UI/Upgrade Icons/FluidHyperdynamics": "Vent5.png",
        "UI/Upgrade Icons/FractalPiping": "Exchanger5.png",
        "UI/Upgrade Icons/ExpCapacitor": "Capacitor6.png",
        "UI/Upgrade Icons/VortexCooling": "Coolant6.png",
        "UI/Upgrade Icons/Ultracryonics": "Coolant5.png",
        "UI/Upgrade Icons/PhlebotinumCore": "Plate5.png",
        "UI/Upgrade Icons/RefactoredProtium": "Fuel7-2.png",
        "UI/Upgrade Icons/Monastium": "Fuel8-1.png",
        "UI/Upgrade Icons/Kymium": "Fuel9-1.png",
        "UI/Upgrade Icons/Discurrium": "Fuel10-1.png",
        "UI/Upgrade Icons/Stavrium": "Fuel11-1.png",
        # Category overlay sprites
        "UI/Upgrade Icons/GenericPlus": "GenericPlus.png",
        "UI/Upgrade Icons/GenericPower": "GenericPower.png",
        "UI/Upgrade Icons/GenericInfinity": "GenericInfinity.png",
        "UI/Upgrade Icons/GenericHeat": "GenericHeat.png",
    }
    upgrade_sprites = {}
    for icon_path, sprite_name in _UPGRADE_ICON_MAP.items():
        if icon_path in upgrade_sprites:
            continue
        try:
            upgrade_sprites[icon_path] = _load_texture(sprite_name)
        except Exception:
            pass

    ui = Ui(
        heat_icon=heat_icon,
        power_icon=power_icon,
        btn_big=btn_big,
        btn_big_hover=btn_big_hover,
        btn_big_pressed=btn_big_pressed,
        btn_med=btn_med,
        btn_med_hover=btn_med_hover,
        btn_med_pressed=btn_med_pressed,
        btn_small=btn_small,
        btn_small_hover=btn_small_hover,
        btn_small_pressed=btn_small_pressed,
        icon_button=icon_btn,
        icon_button_hover=icon_btn_hover,
        icon_button_pressed=icon_btn_pressed,
        icon_button_locked=icon_btn_locked,
        store_block=store_block,
        store_tab_power=tab_power,
        store_tab_power_hover=tab_power_hover,
        store_tab_power_pressed=tab_power_pressed,
        store_tab_heat=tab_heat,
        store_tab_heat_hover=tab_heat_hover,
        store_tab_heat_pressed=tab_heat_pressed,
        store_tab_experimental=tab_experimental,
        store_tab_experimental_hover=tab_experimental_hover,
        store_tab_experimental_pressed=tab_experimental_pressed,
        store_tab_arcane=tab_arcane,
        store_tab_arcane_hover=tab_arcane_hover,
        store_tab_arcane_pressed=tab_arcane_pressed,
        top_banner=top_banner,
        upgrade_sprites=upgrade_sprites,
    )
    ui.component_sprites = component_sprites

    if _WEB:
        ui.save_dir = True  # Signals web mode for export/import buttons
    else:
        ui.save_dir = save_path.parent
    load_game(sim, save_path)

    show_reference = False
    reference_index = 0
    last_sell_cell = None
    prev_mx, prev_my = 0.0, 0.0

    if _WEB:
        last_save_time = get_time()
        AUTO_SAVE_INTERVAL = 30.0  # Auto-save every 30 seconds

    try:
        while True:
            if not _WEB and WindowShouldClose():
                break

            dt = get_frame_time()
            view_mode_before_input = sim.view_mode

            # Check for pending file import (queued by JS file input)
            if _WEB:
                pending_import = get_pending_file_import()
                if pending_import is not None:
                    _handle_file_import(str(pending_import))

            # RE fn 10408 lines 387652-387665: ticks only run when
            # NOT manually paused AND no UI panel is open.  Controller+0x28
            # holds a pointer-to-panel; when *it != 0 a panel is active and
            # ticks are suppressed (timer reset to Time.time, preventing
            # catch-up).  Our equivalent: view_mode != "reactor" acts as the
            # implicit panel-open flag.
            if not sim.paused and sim.view_mode == "reactor":
                sim.step(dt)
            else:
                # RE: else branch resets tick timer → prevent accumulated
                # dt from causing a burst of ticks on unpause/panel close.
                sim._tick_accumulator = 0.0

            # Explosion animations run even when paused (cosmetic only)
            sim.update_explosions(dt)

            # ── Keyboard events ──────────────────────────────────────
            if is_key_pressed(KEY_F1) and reference_textures:
                show_reference = not show_reference
            if is_key_pressed(KEY_F2) and reference_textures:
                reference_index = (reference_index + 1) % len(reference_textures)
            if is_key_pressed(KEY_F3) and reference_textures:
                reference_index = (reference_index - 1) % len(reference_textures)

            # F4 = take debug screenshot (saved to CWD by raylib)
            if not _WEB and is_key_pressed(KEY_F4):
                import time
                ts = time.strftime("%Y%m%d_%H%M%S")
                fname = f"debug_{sim.view_mode}_{ts}.png"
                take_screenshot(fname)

            # Space = toggle pause (RE: line 387964, writes PauseButton.IsToggled)
            if is_key_pressed(KEY_SPACE):
                sim.paused = not sim.paused

            # ESC: close overlay if open, else deselect (RE: line 387898-387908)
            if is_key_pressed(KEY_ESCAPE):
                if sim.view_mode != "reactor":
                    sim.view_mode = "reactor"
                else:
                    sim.selected_component_index = -1

            # X: debug — multiply money by 10
            if not _WEB and is_key_pressed(KEY_X):
                sim.store.money = max(sim.store.money * 10, 10.0)

            # Y: debug — multiply exotic particles by 10
            if not _WEB and is_key_pressed(KEY_Y):
                sim.store.exotic_particles = max(sim.store.exotic_particles * 10, 10.0)

            # ── Mouse state ──────────────────────────────────────────
            mouse = get_mouse_position()
            mx = mouse.x if hasattr(mouse, "x") else mouse[0]
            my = mouse.y if hasattr(mouse, "y") else mouse[1]

            # ── Scroll / pan input (before grid clicks) ────────────────
            scrollbar_consumed = False
            if sim.view_mode == "reactor" and sim.grid is not None:
                wheel = get_mouse_wheel_move()
                middle_down = is_mouse_button_down(MOUSE_BUTTON_MIDDLE)
                scrollbar_consumed = sim.grid.handle_scrollbar_drag(
                    mx, my,
                    mouse_down=is_mouse_button_down(MOUSE_BUTTON_LEFT),
                    mouse_pressed=is_mouse_button_pressed(MOUSE_BUTTON_LEFT),
                )
                # Left-click drag pans when no shop component is selected
                # and cursor is within the grid viewport
                g = sim.grid
                in_grid = (g.origin_x <= mx < g.origin_x + g.viewport_w and
                           g.origin_y <= my < g.origin_y + g.viewport_h)
                left_pan = (
                    is_mouse_button_down(MOUSE_BUTTON_LEFT)
                    and not scrollbar_consumed
                    and sim.selected_component_index == -1
                    and g.needs_scroll
                    and in_grid
                )
                sim.grid.handle_scroll_input(
                    mx, my, prev_mx, prev_my,
                    middle_down=middle_down or left_pan,
                    wheel_move=wheel,
                )

            # ── Button: Vent Heat ─────────────────────────────────────
            bw, bh = btn_big.width, btn_big.height
            vent_x, vent_y = layout.vent_x, layout.vent_y
            hover_vent = vent_x <= mx <= vent_x + bw and vent_y <= my <= vent_y + bh
            pressed_vent = hover_vent and is_mouse_button_down(MOUSE_BUTTON_LEFT)
            if hover_vent and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                sim.vent_heat()

            # ── Button: Sell Power / Scrounge ─────────────────────────
            sell_x, sell_y = layout.sell_x, layout.sell_y
            hover_sell = sell_x <= mx <= sell_x + bw and sell_y <= my <= sell_y + bh
            pressed_sell = hover_sell and is_mouse_button_down(MOUSE_BUTTON_LEFT)
            if hover_sell and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                sim.sell_or_scrounge()

            # ── Button: Pause (toggle) ────────────────────────────────
            pw, ph = btn_play.width, btn_play.height
            hover_pause = (layout.pause_x <= mx <= layout.pause_x + pw and
                           layout.pause_y <= my <= layout.pause_y + ph)
            pressed_pause = hover_pause and is_mouse_button_down(MOUSE_BUTTON_LEFT)
            if hover_pause and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                sim.paused = not sim.paused

            # ── Button: Replace (toggle) ──────────────────────────────
            rw, rh = btn_replace.width, btn_replace.height
            hover_replace = (layout.replace_x <= mx <= layout.replace_x + rw and
                             layout.replace_y <= my <= layout.replace_y + rh)
            pressed_replace = hover_replace and is_mouse_button_down(MOUSE_BUTTON_LEFT)
            if hover_replace and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                sim.replace_mode = not sim.replace_mode

            # ── Button: Upgrades ───────────────────────────────────────
            uw, uh = btn_med.width, btn_med.height
            hover_upgrades = (layout.main_upgrades_x <= mx <= layout.main_upgrades_x + uw and
                              layout.main_upgrades_y <= my <= layout.main_upgrades_y + uh)
            if hover_upgrades and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                if sim.view_mode == "upgrades":
                    sim.view_mode = "reactor"
                else:
                    sim.view_mode = "upgrades"

            # ── Button: Prestige ───────────────────────────────────────
            hover_prestige = (layout.prestige_upgrades_x <= mx <= layout.prestige_upgrades_x + uw and
                              layout.prestige_upgrades_y <= my <= layout.prestige_upgrades_y + uh)
            if hover_prestige and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                if sim.view_mode == "prestige":
                    sim.view_mode = "reactor"
                else:
                    sim.view_mode = "prestige"

            # ── Button: Back ───────────────────────────────────────────
            bkw, bkh = btn_back.width, btn_back.height
            hover_back = (layout.back_x <= mx <= layout.back_x + bkw and
                          layout.back_y <= my <= layout.back_y + bkh)
            if hover_back and is_mouse_button_released(MOUSE_BUTTON_LEFT):
                sim.view_mode = "reactor"

            # ── Bottom buttons: Options / Statistics / Help ───────────
            sw, sh = btn_small.width, btn_small.height

            hover_options = (layout.options_x <= mx <= layout.options_x + sw and
                             layout.options_y <= my <= layout.options_y + sh)
            if hover_options and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                sim.view_mode = "reactor" if sim.view_mode == "options" else "options"

            hover_stats = (layout.stats_x_btn <= mx <= layout.stats_x_btn + sw and
                           layout.stats_y_btn <= my <= layout.stats_y_btn + sh)
            if hover_stats and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                sim.view_mode = "reactor" if sim.view_mode == "statistics" else "statistics"

            hover_help = (layout.help_x <= mx <= layout.help_x + sw and
                          layout.help_y <= my <= layout.help_y + sh)
            if hover_help and is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
                if sim.view_mode == "help":
                    sim.view_mode = "reactor"
                else:
                    sim.view_mode = "help"
                    ui.help_scroll_y = 0.0
                    ui.help_drag_active = False

            # Clear confirmation state when navigating away from options
            # RE: fn 10489 — binary clears all confirmation state on navigation
            if sim.view_mode != "options":
                sim.prestige_confirming = False
                sim.prestige_can_refund = False
                sim.reset_confirm_timer = 0.0

            # ── Grid: Left click — place / replace (reactor view only) ──
            if (
                view_mode_before_input == "reactor"
                and sim.view_mode == "reactor"
                and sim.grid is not None
                and is_mouse_button_down(MOUSE_BUTTON_LEFT)
                and not scrollbar_consumed
            ):
                if 0 <= mx < layout.window_width and 0 <= my < layout.window_height:
                    cell = sim.grid.screen_to_cell(int(mx), int(my))
                else:
                    cell = sim.grid.screen_to_cell_unbounded(int(mx), int(my))
                if cell is not None:
                    cx, cy = cell
                    selected = sim.selected_component()
                    if selected is not None:
                        existing = sim.grid.get(cx, cy, 0)
                        if existing is None:
                            # Empty cell: place if affordable
                            cost = sim.get_component_cost(selected)
                            if is_key_down(KEY_SHIFT):
                                for (mcx, mcy, _, mcomp) in sim.grid.iter_cells():
                                    if not mcomp:
                                        if sim.store.money >= cost:
                                            if sim.place_component(mcx, mcy, ReactorComponent(stats=selected), recompute_capacities=False):
                                                sim.store.money -= cost
                                        else:
                                            break
                                sim.recompute_max_capacities()
                            else: 
                                if sim.store.money >= cost:
                                    if sim.place_component(cx, cy, ReactorComponent(stats=selected)):
                                        sim.store.money -= cost
                        elif Simulation.can_replace(existing, selected):
                            # Click-to-replace: always active for compatible components
                            cost = sim.get_component_cost(selected)
                            refund = Simulation.sell_value(existing)

                            if is_key_down(KEY_SHIFT):
                                existing_name = existing.stats.name
                                for (mcx, mcy, _, mcomp) in sim.grid.iter_cells():
                                    if mcomp and mcomp.stats.name == existing_name:
                                        if sim.store.money + refund >= cost:
                                            sim.remove_component(mcx, mcy, recompute_capacities=False)
                                            sim.store.money += refund
                                            if sim.place_component(mcx, mcy, ReactorComponent(stats=selected), recompute_capacities=False):
                                                sim.store.money -= cost
                                        else:
                                            break
                                sim.recompute_max_capacities()
                            else:
                                if sim.store.money + refund >= cost:
                                    sim.remove_component(cx, cy)
                                    sim.store.money += refund
                                    if sim.place_component(cx, cy, ReactorComponent(stats=selected)):
                                        sim.store.money -= cost

            # ── Right click — sell / deselect ────────────────────────
            # RE: Two deselect paths in unnamed_function_10410:
            #   1. Mouse NOT over grid cell + RMB down → deselect (line 388001)
            #   2. Mouse over empty grid cell + RMB down → deselect (line 388094)
            #   Mouse over occupied grid cell + RMB held → sell (line 388084)
            if (
                view_mode_before_input == "reactor"
                and sim.view_mode == "reactor"
                and sim.grid is not None
                and is_mouse_button_down(MOUSE_BUTTON_RIGHT)
            ):
                if 0 <= mx < layout.window_width and 0 <= my < layout.window_height:
                    cell = sim.grid.screen_to_cell(int(mx), int(my))
                else:
                    cell = sim.grid.screen_to_cell_unbounded(int(mx), int(my))
                if cell is not None:
                    if cell != last_sell_cell:
                        cx, cy = cell
                        existing = sim.grid.get(cx, cy, 0)
                        if existing is not None:
                            refund = Simulation.sell_value(existing)
                            if is_key_down(KEY_SHIFT):
                                existing_name = existing.stats.name
                                for (mcx, mcy, _, mcomp) in sim.grid.iter_cells():
                                    if mcomp and mcomp.stats.name == existing_name:
                                        sim.remove_component(mcx, mcy, recompute_capacities=False)
                                        sim.store.money += refund
                                sim.recompute_max_capacities()
                            else:
                                sim.remove_component(cx, cy)
                                sim.store.money += refund
                        elif is_mouse_button_pressed(MOUSE_BUTTON_RIGHT):
                            sim.selected_component_index = -1
                        last_sell_cell = cell
                else:
                    # Not over grid: deselect on press (line 388001-388004)
                    if is_mouse_button_pressed(MOUSE_BUTTON_RIGHT):
                        sim.selected_component_index = -1
            elif not is_mouse_button_down(MOUSE_BUTTON_RIGHT):
                last_sell_cell = None

            # Keep upgrade effects and per-tick preview metrics current even when
            # ticks are suppressed (paused / non-reactor views).
            sim.refresh_live_preview()

            begin_drawing()
            clear_background(Color(18, 18, 22, 255))

            if show_reference and reference_textures:
                ref_tex, ref_name = reference_textures[reference_index]
                src = Rectangle(0, 0, ref_tex.width, ref_tex.height)
                dst = Rectangle(0, 0, layout.window_width, layout.window_height)
                origin = Vector2(0, 0)
                draw_texture_pro(ref_tex, src, dst, origin, 0.0, Color(255, 255, 255, 160))
                draw_text(
                    f"Reference: {ref_name}",
                    16,
                    layout.window_height - 20,
                    12,
                    Color(200, 200, 200, 255),
                )

            draw_texture_pro(
                side_grid,
                Rectangle(0, 0, side_grid.width, side_grid.height),
                Rectangle(
                    layout.left_panel_x,
                    layout.left_panel_y,
                    layout.left_panel_w,
                    layout.left_panel_h,
                ),
                Vector2(0, 0),
                0.0,
                Color(255, 255, 255, 255),
            )

            frame_x = layout.grid_frame_x
            frame_y = layout.grid_frame_y
            backer_x = frame_x + layout.grid_backer_offset_x
            backer_y = frame_y + layout.grid_backer_offset_y

            if sim.view_mode == "upgrades" or sim.view_mode == "prestige":
                draw_texture_pro(
                    main_upgrades,
                    Rectangle(0, 0, main_upgrades.width, main_upgrades.height),
                    Rectangle(frame_x, frame_y, main_upgrades.width, main_upgrades.height),
                    Vector2(0, 0),
                    0.0,
                    Color(255, 255, 255, 255),
                )
            else:
                draw_texture_pro(
                    grid_backer,
                    Rectangle(0, 0, grid_backer.width, grid_backer.height),
                    Rectangle(backer_x, backer_y, grid_backer.width, grid_backer.height),
                    Vector2(0, 0),
                    0.0,
                    Color(255, 255, 255, 255),
                )

            if sim.view_mode == "reactor":
                # ── Reactor grid view ──────────────────────────────────
                grid.draw(Color(255, 255, 255, 255))

                use_scissor = sim.grid is not None and sim.grid.needs_scroll

                if use_scissor:
                    begin_scissor_mode(
                        sim.grid.origin_x, sim.grid.origin_y,
                        sim.grid.viewport_w, sim.grid.viewport_h,
                    )

                if sim.grid is not None:
                    cell_sz = sim.grid.cell_size
                    bar_h = max(2, cell_sz // 10)
                    bar_margin = 2
                    bar_w = cell_sz - bar_margin * 2

                    for gx, gy, _gz, component in sim.grid.iter_cells():
                        if component is None:
                            continue
                        tex = component_sprites.get(component.stats.sprite_name)
                        if tex is None:
                            continue
                        px, py = sim.grid.cell_to_screen(gx, gy)
                        scale = min(1.0, cell_sz / max(1, tex.width), cell_sz / max(1, tex.height))
                        draw_w = tex.width * scale
                        draw_h = tex.height * scale
                        offset_x = (cell_sz - draw_w) * 0.5
                        offset_y = (cell_sz - draw_h) * 0.5

                        if component.depleted:
                            tint = Color(180, 180, 180, 255)
                        else:
                            tint = Color(255, 255, 255, 255)

                        draw_texture_pro(
                            tex,
                            Rectangle(0, 0, tex.width, tex.height),
                            Rectangle(px + offset_x, py + offset_y, draw_w, draw_h),
                            Vector2(0, 0),
                            0.0,
                            tint,
                        )
                        if (
                            not component.depleted
                            and component.stats.type_of_component == "Outlet"
                            and sim.is_outlet_bottleneck(component)
                        ):
                            badge_size = max(9, min(13, cell_sz // 3))
                            badge_x = int(px + cell_sz - badge_size - 2)
                            badge_y = int(py + 2)
                            ui.draw_warning_badge(badge_x, badge_y, badge_size)

                        bars_drawn = 0
                        stats = component.stats

                        eff_hc = sim.get_effective_heat_capacity(component)
                        if eff_hc > 0.0 and not component.depleted:
                            raw_fill = component.heat / eff_hc
                            fill = min(1.0, max(0.0, raw_fill))
                            bar_y = py + cell_sz - bar_margin - bar_h
                            if raw_fill > 0.8:
                                pulse = abs(math.sin(get_time() * 6.0))
                                bg_r = int(40 + 80 * pulse)
                                draw_rectangle(
                                    int(px + bar_margin), int(bar_y),
                                    int(bar_w), int(bar_h),
                                    Color(bg_r, 10, 10, 200),
                                )
                            else:
                                draw_rectangle(
                                    int(px + bar_margin), int(bar_y),
                                    int(bar_w), int(bar_h),
                                    Color(40, 10, 10, 180),
                                )
                            if fill > 0.001:
                                if raw_fill > 0.8:
                                    pulse = abs(math.sin(get_time() * 6.0))
                                    r = 255
                                    g = int(120 + 135 * pulse)
                                    b = int(60 + 195 * pulse)
                                else:
                                    r = int(180 + 75 * fill)
                                    g = int(60 * (1.0 - fill))
                                    b = 20
                                draw_rectangle(
                                    int(px + bar_margin), int(bar_y),
                                    int(bar_w * fill), int(bar_h),
                                    Color(min(255, r), g, b, 220),
                                )
                            bars_drawn += 1

                        eff_dur = sim.get_effective_max_durability(component)
                        if eff_dur > 0.0:
                            fill = min(1.0, max(0.0, component.durability / eff_dur))
                            bar_y = py + cell_sz - bar_margin - bar_h * (bars_drawn + 1) - bars_drawn
                            draw_rectangle(
                                int(px + bar_margin), int(bar_y),
                                int(bar_w), int(bar_h),
                                Color(10, 30, 10, 180),
                            )
                            if fill > 0.001:
                                if fill > 0.5:
                                    r = int(220 * (1.0 - fill) * 2)
                                    g = 200
                                else:
                                    r = 220
                                    g = int(200 * fill * 2)
                                draw_rectangle(
                                    int(px + bar_margin), int(bar_y),
                                    int(bar_w * fill), int(bar_h),
                                    Color(r, g, 30, 220),
                                )
                            bars_drawn += 1

                # Draw explosion animations on top of grid cells
                if sim.grid is not None:
                    cell_sz = sim.grid.cell_size
                    for effect in sim.explosions:
                        if 0 <= effect.frame < 12:
                            ex_tex = explosion_textures[effect.frame]
                            ex_scale = cell_sz / max(1, ex_tex.height)
                            ex_w = ex_tex.width * ex_scale
                            ex_h = ex_tex.height * ex_scale
                            ex_ox = (cell_sz - ex_w) * 0.5
                            ex_oy = (cell_sz - ex_h) * 0.5
                            ex_sx, ex_sy = sim.grid.cell_to_screen(effect.grid_x, effect.grid_y)
                            draw_texture_pro(
                                ex_tex,
                                Rectangle(0, 0, ex_tex.width, ex_tex.height),
                                Rectangle(ex_sx + ex_ox, ex_sy + ex_oy, ex_w, ex_h),
                                Vector2(0, 0),
                                0.0,
                                Color(255, 255, 255, 255),
                            )

                if use_scissor:
                    end_scissor_mode()

            elif sim.view_mode in ("upgrades", "prestige"):
                # ── Upgrade/Prestige grid view ─────────────────────────
                ui.draw_upgrade_grid(
                    sim, layout,
                    mouse_x=mx, mouse_y=my,
                    mouse_down=is_mouse_button_down(MOUSE_BUTTON_LEFT),
                    mouse_released=is_mouse_button_released(MOUSE_BUTTON_LEFT)
                )
            elif sim.view_mode == "statistics":
                ui.draw_statistics_panel(sim, layout)
            elif sim.view_mode == "options":
                ui.draw_options_panel(sim, layout, mouse_x=mx, mouse_y=my,
                                      mouse_down=is_mouse_button_down(MOUSE_BUTTON_LEFT),
                                      mouse_released=is_mouse_button_released(MOUSE_BUTTON_LEFT),
                                      dt=dt)
            elif sim.view_mode == "help":
                ui.draw_help_panel(sim, layout, wheel_move=get_mouse_wheel_move(), mouse_x=mx, mouse_y=my, mouse_down=is_mouse_button_down(MOUSE_BUTTON_LEFT))

            draw_texture_pro(
                grid_frame,
                Rectangle(0, 0, grid_frame.width, grid_frame.height),
                Rectangle(frame_x, frame_y, grid_frame.width, grid_frame.height),
                Vector2(0, 0),
                0.0,
                Color(255, 255, 255, 255),
            )

            # Scrollbars drawn on top of grid frame so they're visible
            if sim.view_mode == "reactor" and sim.grid is not None:
                sim.grid.draw_scrollbars()

            def draw_button_label(label: str, x: int, y: int, w: int, h: int, base_size: int, min_size: int = 8, pressed: bool = False) -> None:
                fs = base_size
                max_w = max(8, w - 10)
                while fs > min_size and measure_text(label, fs) > max_w:
                    fs -= 1
                tw = measure_text(label, fs)
                tx = x + max(0, (w - tw) // 2)
                ty = y + max(0, (h - fs) // 2)
                if pressed: ty += 2
                draw_text(label, tx, ty, fs, Color(220, 220, 220, 255))

            # Top-left upgrade tabs
            ui.draw_button(layout.main_upgrades_x, layout.main_upgrades_y, sim.view_mode == "upgrades", hover_upgrades, "Upgrades", 2)
            ui.draw_button(layout.prestige_upgrades_x, layout.prestige_upgrades_y, sim.view_mode == "prestige", hover_prestige, "Prestige", 2)

            # Bottom buttons (Options / Statistics / Help)
            ui.draw_button(layout.options_x, layout.options_y, sim.view_mode == "options", hover_prestige, "Options", 1)
            ui.draw_button(layout.stats_x_btn, layout.stats_y_btn, sim.view_mode == "statistics", hover_prestige, "Statistics", 1)
            ui.draw_button(layout.help_x, layout.help_y, sim.view_mode == "help", hover_prestige, "Help", 1)

            # Back button (visible only when in upgrade/prestige view)
            if sim.view_mode != "reactor":
                btn_pressed = hover_back and is_mouse_button_down(MOUSE_BUTTON_LEFT)
                back_tex = btn_back_pressed if btn_pressed else (btn_back_hover if hover_back else btn_back)
                draw_texture_pro(
                    back_tex,
                    Rectangle(0, 0, back_tex.width, back_tex.height),
                    Rectangle(layout.back_x, layout.back_y, back_tex.width, back_tex.height),
                    Vector2(0, 0),
                    0.0,
                    Color(255, 255, 255, 255),
                )
                # ButtonBACK is 96x46; arrow takes ~20px on left, center text in remaining area
                back_text_x = layout.back_x + 20 + (96 - 20 - measure_text("Back", 14)) // 2
                ty = layout.back_y + 16
                if btn_pressed: ty += 2
                draw_text("Back", back_text_x, ty, 14, Color(220, 220, 220, 255))

            # Grid hover (fallback — shop hover in ui.draw() will override)
            sim.hover_component = None
            sim.hover_placed_component = None
            if sim.view_mode == "reactor" and sim.grid is not None:
                cell = sim.grid.screen_to_cell(int(mx), int(my))
                if cell is not None:
                    cx, cy = cell
                    grid_comp = sim.grid.get(cx, cy, 0)
                    if grid_comp is not None:
                        sim.hover_component = grid_comp.stats
                        sim.hover_placed_component = grid_comp

            ui.draw(
                sim,
                layout,
                hover_vent=hover_vent,
                pressed_vent=pressed_vent,
                hover_sell=hover_sell,
                pressed_sell=pressed_sell,
                mouse_x=mx,
                mouse_y=my,
                mouse_pressed=is_mouse_button_pressed(MOUSE_BUTTON_LEFT),
            )

            # Store icon overlay (draw after slots so icons sit on top)
            shop_components = sim.shop_components_for_page()
            slots = ui.store_item_slots(layout, shop_components)
            for _idx, (comp, rect) in enumerate(slots):
                tex = component_sprites.get(comp.sprite_name)
                if tex is None:
                    continue
                x, y, w, h = rect
                draw_w = tex.width
                draw_h = tex.height
                icon_x = x + (w - draw_w) / 2
                icon_y = y + (h - draw_h) / 2
                eff_cost = sim.get_component_cost(comp)
                if _idx == sim.selected_component_index and sim.store.money >= eff_cost:
                    tint = Color(120, 255, 120, 255)  # green tint for selected + affordable
                elif eff_cost > 0.0 and sim.store.money < eff_cost:
                    tint = Color(160, 160, 160, 255)  # dim for unaffordable
                else:
                    tint = Color(255, 255, 255, 255)
                draw_texture_pro(
                    tex,
                    Rectangle(0, 0, tex.width, tex.height),
                    Rectangle(icon_x, icon_y, draw_w, draw_h),
                    Vector2(0, 0),
                    0.0,
                    tint,
                )

            # Top-right toggle buttons (drawn after ui.draw so they render
            # on top of the info banner)
            # Play/Pause: PLAY sprites when running, PAUSE sprites when paused
            if sim.paused:
                if pressed_pause:
                    pause_tex = btn_pause_pressed
                elif hover_pause:
                    pause_tex = btn_pause_hover
                else:
                    pause_tex = btn_pause
            else:
                if pressed_pause:
                    pause_tex = btn_play_pressed
                elif hover_pause:
                    pause_tex = btn_play_hover
                else:
                    pause_tex = btn_play
            draw_texture_pro(
                pause_tex,
                Rectangle(0, 0, pause_tex.width, pause_tex.height),
                Rectangle(layout.pause_x, layout.pause_y, pause_tex.width, pause_tex.height),
                Vector2(0, 0),
                0.0,
                Color(255, 255, 255, 255),
            )

            # Replace/NoReplace: REPLACE sprites when on, NOREPLACE when off
            if sim.replace_mode:
                if pressed_replace:
                    replace_tex = btn_replace_pressed
                elif hover_replace:
                    replace_tex = btn_replace_hover
                else:
                    replace_tex = btn_replace
            else:
                if pressed_replace:
                    replace_tex = btn_noreplace_pressed
                elif hover_replace:
                    replace_tex = btn_noreplace_hover
                else:
                    replace_tex = btn_noreplace
            draw_texture_pro(
                replace_tex,
                Rectangle(0, 0, replace_tex.width, replace_tex.height),
                Rectangle(layout.replace_x, layout.replace_y, replace_tex.width, replace_tex.height),
                Vector2(0, 0),
                0.0,
                Color(255, 255, 255, 255),
            )

            end_drawing()
            prev_mx, prev_my = mx, my

            # Periodic auto-save (web: no reliable finally; native: also nice to have)
            if _WEB:
                now = get_time()
                if now - last_save_time >= AUTO_SAVE_INTERVAL:
                    save_game(sim, save_path)
                    last_save_time = now

            # Yield to browser event loop
            if _WEB:
                await _wait_frame()  # vsync via requestAnimationFrame Promise
            else:
                await asyncio.sleep(0)
    finally:
        save_game(sim, save_path)
        if not _WEB:
            unload_texture(heat_tex)
            unload_texture(power_tex)
            unload_texture(grid_tex)
            unload_texture(grid_full)
            unload_texture(grid_backer)
            unload_texture(grid_frame)
            unload_texture(side_grid)
            unload_texture(btn_big)
            unload_texture(btn_big_hover)
            unload_texture(btn_big_pressed)
            unload_texture(btn_small)
            unload_texture(btn_small_hover)
            unload_texture(btn_small_pressed)
            unload_texture(btn_med)
            unload_texture(btn_med_hover)
            unload_texture(btn_med_pressed)
            unload_texture(btn_back)
            unload_texture(btn_back_hover)
            unload_texture(btn_back_pressed)
            unload_texture(btn_play)
            unload_texture(btn_play_hover)
            unload_texture(btn_play_pressed)
            unload_texture(btn_pause)
            unload_texture(btn_pause_hover)
            unload_texture(btn_pause_pressed)
            unload_texture(btn_replace)
            unload_texture(btn_replace_hover)
            unload_texture(btn_replace_pressed)
            unload_texture(btn_noreplace)
            unload_texture(btn_noreplace_hover)
            unload_texture(btn_noreplace_pressed)
            unload_texture(top_banner)
            unload_texture(store_block)
            unload_texture(tab_power)
            unload_texture(tab_power_hover)
            unload_texture(tab_power_pressed)
            unload_texture(tab_heat)
            unload_texture(tab_heat_hover)
            unload_texture(tab_heat_pressed)
            unload_texture(tab_experimental)
            unload_texture(tab_experimental_hover)
            unload_texture(tab_experimental_pressed)
            unload_texture(tab_arcane)
            unload_texture(tab_arcane_hover)
            unload_texture(tab_arcane_pressed)
            unload_texture(icon_btn)
            unload_texture(icon_btn_hover)
            unload_texture(icon_btn_pressed)
            unload_texture(icon_btn_locked)
            for tex in explosion_textures:
                unload_texture(tex)
            for tex in component_sprites.values():
                unload_texture(tex)
            for tex, _ in reference_textures:
                unload_texture(tex)
            close_window()

def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
