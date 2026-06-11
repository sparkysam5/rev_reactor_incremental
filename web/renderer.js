/**
 * renderer.js — Canvas2D batch renderer for Rev Reactor web port.
 *
 * Reads a Float64Array of draw commands from Python and executes them
 * as Canvas2D calls.  One FFI call per frame instead of ~300.
 *
 * Opcodes:
 *   0  CLEAR_BG          r,g,b,a                          (5 values)
 *   1  FILL_RECT         x,y,w,h,r,g,b,a                 (9 values)
 *   2  STROKE_RECT       x,y,w,h,r,g,b,a                 (9 values)
 *   3  DRAW_TEXT         strIdx,x,y,size,r,g,b,a         (9 values)
 *   4  TEXTURE_PRO       texId,sx,sy,sw,sh,dx,dy,dw,dh,r,g,b,a  (14 values)
 *   5  TEXTURE_EX        texId,x,y,rot,scale,r,g,b,a    (10 values)
 *   6  BEGIN_SCISSOR     x,y,w,h                         (5 values)
 *   7  END_SCISSOR       (none)                           (1 value)
 *   8  DRAW_TEXT_LIGHT   strIdx,x,y,size,r,g,b,a         (9 values)
 */

globalThis.Renderer = (() => {
    const canvas = document.getElementById('game-canvas');
    const ctx = canvas.getContext('2d');

    // DPR-scale canvas: internal resolution matches physical pixels,
    // CSS size stays at logical 900x630.  imageSmoothingEnabled=false
    // gives nearest-neighbor for sprites; text uses subpixel AA naturally.
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.round(900 * dpr);
    canvas.height = Math.round(630 * dpr);
    canvas.style.width = '900px';
    canvas.style.height = '630px';
    canvas.style.boxSizing = 'content-box';   // border must not eat into 900x630
    ctx.scale(dpr, dpr);
    ctx.imageSmoothingEnabled = false;

    // Texture registry: id -> { img, name }
    const textures = {};       // id -> Image
    const texturesByName = {}; // name -> { id, img }
    let nextId = 1;

    // Emscripten Module reference for zero-copy command buffer reads
    let _emModule = null;

    function setWasmMemory(mod) {
        _emModule = mod;
    }

    // Temp canvas for color tinting (object reused across calls)
    let _tintCanvas = null;
    let _tintCtx = null;
    const _tintResult = { canvas: null, ctx: null };

    function getTintCanvas(w, h) {
        if (!_tintCanvas) {
            _tintCanvas = document.createElement('canvas');
            _tintCtx = _tintCanvas.getContext('2d');
            _tintResult.canvas = _tintCanvas;
            _tintResult.ctx = _tintCtx;
        }
        if (_tintCanvas.width < w) _tintCanvas.width = w;
        if (_tintCanvas.height < h) _tintCanvas.height = h;
        return _tintResult;
    }

    function registerTexture(name, img) {
        const id = nextId++;
        textures[id] = img;
        texturesByName[name] = { id, img };
        return id;
    }

    function getTextureInfo(name) {
        const entry = texturesByName[name];
        if (!entry) return null;
        return {
            id: entry.id,
            width: entry.img.naturalWidth || entry.img.width,
            height: entry.img.naturalHeight || entry.img.height,
        };
    }

    function swapTexture(name, newImg) {
        const entry = texturesByName[name];
        if (!entry) return;
        textures[entry.id] = newImg;
        entry.img = newImg;
    }

    // String caches — avoid per-frame template literal allocations
    const _rgbaCache = new Map();
    const _rgbCache = new Map();
    const _fontCache = new Map();

    function rgba(r, g, b, a) {
        const key = ((r|0) << 24) | ((g|0) << 16) | ((b|0) << 8) | (a|0);
        let s = _rgbaCache.get(key);
        if (s === undefined) {
            s = `rgba(${r|0},${g|0},${b|0},${(a/255).toFixed(3)})`;
            _rgbaCache.set(key, s);
        }
        return s;
    }

    function rgb(r, g, b) {
        const key = ((r|0) << 16) | ((g|0) << 8) | (b|0);
        let s = _rgbCache.get(key);
        if (s === undefined) {
            s = `rgb(${r|0},${g|0},${b|0})`;
            _rgbCache.set(key, s);
        }
        return s;
    }

    function getFont(size, weight=400) {
        const s = size | 0;
        let f = _fontCache.get(`${s}-${weight}`);
        if (f === undefined) {
            f = `400 ${s}px "JetBrains Mono", "Consolas", "Courier New", monospace`;
            _fontCache.set(s, f);
        }
        return f;
    }

    function measureTextWidth(text, size) {
        ctx.font = getFont(size);
        return ctx.measureText(text).width;
    }

    /**
     * Draw a texture with tint handling.
     * Fast paths for common tint patterns; offscreen composite for color tints.
     */
    function drawImageTinted(img, sx, sy, sw, sh, dx, dy, dw, dh, r, g, b, a) {
        if (r === 255 && g === 255 && b === 255 && a === 255) {
            // No tint — direct draw (fast path, ~90% of calls)
            ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
            return;
        }

        if (r === 255 && g === 255 && b === 255) {
            // Alpha-only tint
            const prev = ctx.globalAlpha;
            ctx.globalAlpha = a / 255;
            ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
            ctx.globalAlpha = prev;
            return;
        }

        // Grayscale dim used to approximate with alpha, but that makes
        // textures transparent instead of darker.  Fall through to the
        // proper multiply-composite path below.

        // Color tint (e.g. green highlight) — offscreen composite
        const tw = Math.ceil(Math.abs(sw));
        const th = Math.ceil(Math.abs(sh));
        if (tw === 0 || th === 0) return;

        const { canvas: tc, ctx: tctx } = getTintCanvas(tw, th);
        tctx.clearRect(0, 0, tw, th);

        // Draw source region
        tctx.globalCompositeOperation = 'source-over';
        tctx.globalAlpha = 1;
        tctx.drawImage(img, sx, sy, sw, sh, 0, 0, tw, th);

        // Multiply with tint color
        tctx.globalCompositeOperation = 'multiply';
        tctx.fillStyle = rgb(r, g, b);
        tctx.fillRect(0, 0, tw, th);

        // Restore alpha from original
        tctx.globalCompositeOperation = 'destination-in';
        tctx.drawImage(img, sx, sy, sw, sh, 0, 0, tw, th);

        // Blit to main canvas
        const prev = ctx.globalAlpha;
        ctx.globalAlpha = a / 255;
        ctx.drawImage(tc, 0, 0, tw, th, dx, dy, dw, dh);
        ctx.globalAlpha = prev;
    }

    /**
     * Main entry point: process the command buffer.
     * @param {number} byteOffset - Byte offset into WASM linear memory
     * @param {number} count - Number of float64 elements in the command buffer
     * @param {Array<string>} strings - String table for text commands
     */
    function renderBatch(byteOffset, count, strings) {
        if (!_emModule) {
            return;
        }
        const elemOffset = byteOffset / 8;
        const cmds = _emModule.HEAPF64.subarray(elemOffset, elemOffset + count);
        const len = cmds.length;
        let i = 0;

        while (i < len) {
            const op = cmds[i++];

            switch (op) {
                case 0: { // CLEAR_BG: r,g,b,a
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    ctx.fillStyle = rgba(r, g, b, a);
                    ctx.fillRect(0, 0, 900, 630);
                    break;
                }
                case 1: { // FILL_RECT: x,y,w,h,r,g,b,a
                    const x = cmds[i++], y = cmds[i++], w = cmds[i++], h = cmds[i++];
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    const prev = ctx.globalAlpha;
                    ctx.globalAlpha = a / 255;
                    ctx.fillStyle = rgb(r, g, b);
                    ctx.fillRect(x, y, w, h);
                    ctx.globalAlpha = prev;
                    break;
                }
                case 2: { // STROKE_RECT: x,y,w,h,r,g,b,a
                    const x = cmds[i++], y = cmds[i++], w = cmds[i++], h = cmds[i++];
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    const prev = ctx.globalAlpha;
                    ctx.globalAlpha = a / 255;
                    ctx.strokeStyle = rgb(r, g, b);
                    ctx.lineWidth = 1;
                    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
                    ctx.globalAlpha = prev;
                    break;
                }
                case 3: { // DRAW_TEXT: strIdx,x,y,size,r,g,b,a
                    const strIdx = cmds[i++] | 0;
                    const x = cmds[i++], y = cmds[i++], size = cmds[i++];
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    ctx.font = getFont(size);
                    ctx.fillStyle = rgba(r, g, b, a);
                    ctx.textBaseline = 'top';
                    ctx.textAlign = 'left';
                    ctx.fillText(strings[strIdx] || '', x, y);
                    break;
                }
                case 4: { // TEXTURE_PRO: texId,sx,sy,sw,sh,dx,dy,dw,dh,r,g,b,a
                    const texId = cmds[i++] | 0;
                    const sx = cmds[i++], sy = cmds[i++], sw = cmds[i++], sh = cmds[i++];
                    const dx = cmds[i++], dy = cmds[i++], dw = cmds[i++], dh = cmds[i++];
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    const img = textures[texId];
                    if (img) {
                        drawImageTinted(img, sx, sy, sw, sh, dx, dy, dw, dh, r, g, b, a);
                    }
                    break;
                }
                case 5: { // TEXTURE_EX: texId,x,y,rot,scale,r,g,b,a
                    const texId = cmds[i++] | 0;
                    const x = cmds[i++], y = cmds[i++];
                    const rot = cmds[i++], scale = cmds[i++];
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    const img = textures[texId];
                    if (img) {
                        const w = img.naturalWidth * scale;
                        const h = img.naturalHeight * scale;
                        if (rot === 0) {
                            drawImageTinted(img, 0, 0, img.naturalWidth, img.naturalHeight,
                                          x, y, w, h, r, g, b, a);
                        } else {
                            ctx.save();
                            ctx.translate(x + w/2, y + h/2);
                            ctx.rotate(rot * Math.PI / 180);
                            drawImageTinted(img, 0, 0, img.naturalWidth, img.naturalHeight,
                                          -w/2, -h/2, w, h, r, g, b, a);
                            ctx.restore();
                        }
                    }
                    break;
                }
                case 6: { // BEGIN_SCISSOR: x,y,w,h
                    const x = cmds[i++], y = cmds[i++], w = cmds[i++], h = cmds[i++];
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(x, y, w, h);
                    ctx.clip();
                    break;
                }
                case 7: { // END_SCISSOR
                    ctx.restore();
                    break;
                }
                case 8: { // DRAW_TEXT_LIGHT: strIdx,x,y,size,r,g,b,a
                    const strIdx = cmds[i++] | 0;
                    const x = cmds[i++], y = cmds[i++], size = cmds[i++];
                    const r = cmds[i++], g = cmds[i++], b = cmds[i++], a = cmds[i++];
                    ctx.font = getFont(size, 200);
                    ctx.fillStyle = rgba(r, g, b, a);
                    ctx.textBaseline = 'top';
                    ctx.textAlign = 'left';
                    ctx.fillText(strings[strIdx] || '', x, y);
                    break;
                }
                default:
                    console.warn('Unknown opcode:', op, 'at index', i - 1);
                    return; // Bail out to avoid desync
            }
        }
    }

    /** Returns a Promise that resolves on the next frame/tick.
     * Visible tab: requestAnimationFrame (vsync).
     * Hidden tab: setTimeout fallback so simulation can keep advancing.
     */
    function waitFrame() {
        if (document.hidden || document.visibilityState !== 'visible') {
            // Hidden tabs throttle timers, but this still allows periodic ticks.
            return new Promise(resolve => setTimeout(resolve, 50));
        }
        return new Promise(resolve => requestAnimationFrame(resolve));
    }

    return {
        canvas,
        ctx,
        registerTexture,
        getTextureInfo,
        swapTexture,
        measureTextWidth,
        renderBatch,
        setWasmMemory,
        waitFrame,
        textures,
        texturesByName,
    };
})();
