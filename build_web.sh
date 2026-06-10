#!/bin/bash
# build_web.sh — Generate the web distribution for Rev Reactor
#
# Creates:
#   web/assets/sprites/  — symlink or copy of sprite PNGs
#   web/manifest.json    — list of all PNG sprite filenames
#   Copies component_types.json to VFS path for catalog loading
#
# Usage: ./build_web.sh [--serve]

set -euo pipefail
cd "$(dirname "$0")"

SPRITES_SRC="../rev_reactor_incremental/decompilation/recovered/recovered_assets/sprites"
SPRITES_CANDY_SRC="../rev_reactor_incremental/decompilation/recovered/recovered_assets/sprites_decayed"
SPRITES_DST="web/assets/sprites"
SPRITES_CANDY_DST="web/assets/sprites_decayed"
ANALYSIS_SRC="../rev_reactor_incremental/decompilation/recovered/recovered_analysis"

echo "=== Rev Reactor Web Build ==="

# 1. Set up sprite assets
if [ ! -d "$SPRITES_SRC" ]; then
    echo "Error: Sprites directory not found at $SPRITES_SRC"
    echo "Expected: ../rev_reactor_incremental/decompilation/recovered/recovered_assets/sprites"
    exit 1
fi

mkdir -p "web/assets"
if [ -L "$SPRITES_DST" ]; then
    rm "$SPRITES_DST"
fi
if [ -d "$SPRITES_DST" ]; then
    echo "Sprites directory already exists, skipping symlink"
else
    ln -s "$(realpath "$SPRITES_SRC")" "$SPRITES_DST"
    echo "Linked sprites: $SPRITES_DST -> $SPRITES_SRC"
fi

# 1b. Set up candy sprite assets (if available)
if [ -d "$SPRITES_CANDY_SRC" ]; then
    if [ -L "$SPRITES_CANDY_DST" ]; then
        rm "$SPRITES_CANDY_DST"
    fi
    if [ ! -d "$SPRITES_CANDY_DST" ]; then
        ln -s "$(realpath "$SPRITES_CANDY_SRC")" "$SPRITES_CANDY_DST"
        echo "Linked candy sprites: $SPRITES_CANDY_DST -> $SPRITES_CANDY_SRC"
    fi
else
    echo "Note: Candy sprites not found at $SPRITES_CANDY_SRC (theme toggle will be inactive)"
fi

# 2. Generate manifest.json (list of all PNG filenames)
echo "Generating manifest.json..."
(cd "$SPRITES_DST" && ls *.png 2>/dev/null) | uv run python -c "
import sys, json
names = [line.strip() for line in sys.stdin if line.strip()]
print(json.dumps(sorted(names), indent=2))
" > web/manifest.json
SPRITE_COUNT=$(uv run python -c "import json; print(len(json.load(open('web/manifest.json'))))")
echo "  $SPRITE_COUNT sprites in manifest.json"

# 3. Copy component_types.json to game/ directory for catalog loading
if [ -f "$ANALYSIS_SRC/component_types.json" ]; then
    cp "$ANALYSIS_SRC/component_types.json" "implementation/src/game/component_types.json"
    echo "Copied component_types.json"
else
    echo "Warning: component_types.json not found, catalog may be incomplete"
fi

# 4. Optionally start local HTTP server
if [ "${1:-}" = "--serve" ]; then
    echo ""
    echo "Starting HTTP server at http://localhost:8080"
    echo "Open http://localhost:8080/web/ in your browser"
    echo "Press Ctrl+C to stop"
    uv run python -m http.server 8080
fi

echo ""
echo "Build complete. To run:"
echo "  cd $(pwd) && npx http-server --cors -p 8080"
echo "  Open http://localhost:8080/web/"
