"""Save/load and export/import system for game state.

Auto-save: JSON to local file (native) or localStorage (web).
Export Old: Reactor Idle-compatible encrypted text (AES-256-CBC via
.NET PasswordDeriveBytes-compatible key derivation), bounded to legacy schema.
Export New: base64-JSON with full reimplementation state (no legacy bounds).
Import: supports raw JSON, base64-JSON, and original encrypted format.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.simulation import Simulation

_WEB = sys.platform == "emscripten"

# ── Original game encryption parameters (RE: RijndaelSimple / Persistence) ──
_PASS_PHRASE = b"nucLERR8CTRzR4n"
_SALT_VALUE = b"superSPP#ci1lZlt"
_INIT_VECTOR = b"@x0B2!3D4ezF607m"
_KEY_SIZE = 256  # bits
_PASSWORD_ITERATIONS = 2
_ORIG_BUILD_VERSION = 24
_ORIG_UPGRADE_COUNT = 51
_ORIG_BASE_GRID_WIDTH = 19
_ORIG_BASE_GRID_HEIGHT = 16
_ORIG_GRID_DEPTH = 1

# Original game component type index → our catalog name (sprite-based).
# RE: component_types.json field_index ordering.
# Legacy save component order from Assembly-CSharp `ComponentTypes` fields:
# fuels, vents, exchangers, inlets, outlets, coolants, reflectors, platings,
# capacitors, ExtremeCapacitor, ExtremeCoolant.
_ORIG_COMPONENT_ORDER = (
    [f"Fuel{tier}-{cores}" for tier in range(1, 12) for cores in (1, 2, 4)]
    + [f"Vent{i}" for i in range(1, 6)]
    + [f"Exchanger{i}" for i in range(1, 6)]
    + [f"Inlet{i}" for i in range(1, 6)]
    + [f"Outlet{i}" for i in range(1, 6)]
    + [f"Coolant{i}" for i in range(1, 6)]
    + [f"Reflector{i}" for i in range(1, 6)]
    + [f"Plate{i}" for i in range(1, 6)]
    + [f"Capacitor{i}" for i in range(1, 6)]
    + ["Capacitor6", "Coolant6"]
)
_ORIG_INDEX_TO_NAME = {idx: name for idx, name in enumerate(_ORIG_COMPONENT_ORDER)}
_ORIG_NAME_TO_INDEX = {name: idx for idx, name in _ORIG_INDEX_TO_NAME.items()}


def _ms_password_derive_bytes(password: bytes, salt: bytes,
                              iterations: int, key_len: int) -> bytes:
    """Replicate .NET PasswordDeriveBytes.GetBytes (SHA1).

    RE: Microsoft reference source — PasswordDeriveBytes.cs.
    ComputeBaseValue: SHA1(password + salt), then iterate (iterations-2) times.
    ComputeBytes: HashPrefix writes counter as ASCII digits (skipped when prefix=0),
    then hashes prefix + baseValue.
    """
    # ComputeBaseValue
    base_value = hashlib.sha1(password + salt).digest()
    for _ in range(1, iterations - 1):
        base_value = hashlib.sha1(base_value).digest()

    # ComputeBytes with HashPrefix
    result = bytearray()
    prefix = 0
    while len(result) < key_len:
        h = hashlib.sha1()
        if prefix > 0:
            # HashPrefix: write counter digits as ASCII bytes
            digits = str(prefix).encode("ascii")
            h.update(digits)
        prefix += 1
        h.update(base_value)
        result.extend(h.digest())

    return bytes(result[:key_len])


def _decrypt_original(ciphertext: bytes) -> str | None:
    """Decrypt an original Reactor Idle save (AES-256-CBC, PKCS7 padding).

    Uses pycryptodome if available, otherwise falls back to a pure-Python
    AES implementation (no external dependencies needed in Pyodide).
    """
    key = _ms_password_derive_bytes(
        _PASS_PHRASE, _SALT_VALUE, _PASSWORD_ITERATIONS, _KEY_SIZE // 8
    )

    decrypted = None

    # Try pycryptodome first (faster, available in desktop builds)
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CBC, _INIT_VECTOR)
        decrypted = cipher.decrypt(ciphertext)
    except ImportError:
        try:
            from Cryptodome.Cipher import AES
            cipher = AES.new(key, AES.MODE_CBC, _INIT_VECTOR)
            decrypted = cipher.decrypt(ciphertext)
        except ImportError:
            pass
    except ValueError as e:
        print(f"[save] AES decryption failed: {e}")
        return None

    # Fallback: pure-Python AES-256-CBC (for Pyodide or when pycryptodome is missing)
    if decrypted is None:
        try:
            decrypted = _aes_cbc_decrypt(key, _INIT_VECTOR, ciphertext)
        except Exception as e:
            print(f"[save] Pure-Python AES decryption failed: {e}")
            return None

    # Validate and strip PKCS7 padding
    if not decrypted:
        return None
    pad = decrypted[-1]
    if pad < 1 or pad > 16:
        return None
    if not all(b == pad for b in decrypted[-pad:]):
        return None

    return decrypted[:-pad].decode("utf-8", errors="replace")


def _encrypt_original(plaintext: str) -> str | None:
    """Encrypt Reactor Idle plaintext save to base64 (AES-256-CBC, PKCS7)."""
    key = _ms_password_derive_bytes(
        _PASS_PHRASE, _SALT_VALUE, _PASSWORD_ITERATIONS, _KEY_SIZE // 8
    )
    data = plaintext.encode("utf-8")
    pad = 16 - (len(data) % 16)
    padded = data + bytes([pad]) * pad

    encrypted = None

    # Try pycryptodome first (faster, available in desktop builds)
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CBC, _INIT_VECTOR)
        encrypted = cipher.encrypt(padded)
    except ImportError:
        try:
            from Cryptodome.Cipher import AES
            cipher = AES.new(key, AES.MODE_CBC, _INIT_VECTOR)
            encrypted = cipher.encrypt(padded)
        except ImportError:
            pass
    except ValueError as e:
        print(f"[save] AES encryption failed: {e}")
        return None

    # Fallback: pure-Python AES-256-CBC (for Pyodide or when pycryptodome is missing)
    if encrypted is None:
        try:
            encrypted = _aes_cbc_encrypt(key, _INIT_VECTOR, padded)
        except Exception as e:
            print(f"[save] Pure-Python AES encryption failed: {e}")
            return None

    return base64.b64encode(encrypted).decode("ascii")


# ── Pure-Python AES-256-CBC (no dependencies) ────────────────────────

def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC decryption, pure Python. Slow but dependency-free."""
    assert len(key) == 32 and len(iv) == 16 and len(data) % 16 == 0
    # Expand key schedule
    rk = _aes_key_expansion(key)
    result = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i:i+16]
        decrypted_block = _aes_decrypt_block(block, rk)
        result.extend(bytes(a ^ b for a, b in zip(decrypted_block, prev)))
        prev = block
    return bytes(result)


def _aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC encryption, pure Python. Slow but dependency-free."""
    assert len(key) == 32 and len(iv) == 16 and len(data) % 16 == 0
    rk = _aes_key_expansion(key)
    result = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        xored = bytes(a ^ b for a, b in zip(block, prev))
        encrypted_block = _aes_encrypt_block(xored, rk)
        result.extend(encrypted_block)
        prev = encrypted_block
    return bytes(result)


# AES S-box and inverse S-box
_SBOX = (
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
)

_INV_SBOX = (
    0x52,0x09,0x6a,0xd5,0x30,0x36,0xa5,0x38,0xbf,0x40,0xa3,0x9e,0x81,0xf3,0xd7,0xfb,
    0x7c,0xe3,0x39,0x82,0x9b,0x2f,0xff,0x87,0x34,0x8e,0x43,0x44,0xc4,0xde,0xe9,0xcb,
    0x54,0x7b,0x94,0x32,0xa6,0xc2,0x23,0x3d,0xee,0x4c,0x95,0x0b,0x42,0xfa,0xc3,0x4e,
    0x08,0x2e,0xa1,0x66,0x28,0xd9,0x24,0xb2,0x76,0x5b,0xa2,0x49,0x6d,0x8b,0xd1,0x25,
    0x72,0xf8,0xf6,0x64,0x86,0x68,0x98,0x16,0xd4,0xa4,0x5c,0xcc,0x5d,0x65,0xb6,0x92,
    0x6c,0x70,0x48,0x50,0xfd,0xed,0xb9,0xda,0x5e,0x15,0x46,0x57,0xa7,0x8d,0x9d,0x84,
    0x90,0xd8,0xab,0x00,0x8c,0xbc,0xd3,0x0a,0xf7,0xe4,0x58,0x05,0xb8,0xb3,0x45,0x06,
    0xd0,0x2c,0x1e,0x8f,0xca,0x3f,0x0f,0x02,0xc1,0xaf,0xbd,0x03,0x01,0x13,0x8a,0x6b,
    0x3a,0x91,0x11,0x41,0x4f,0x67,0xdc,0xea,0x97,0xf2,0xcf,0xce,0xf0,0xb4,0xe6,0x73,
    0x96,0xac,0x74,0x22,0xe7,0xad,0x35,0x85,0xe2,0xf9,0x37,0xe8,0x1c,0x75,0xdf,0x6e,
    0x47,0xf1,0x1a,0x71,0x1d,0x29,0xc5,0x89,0x6f,0xb7,0x62,0x0e,0xaa,0x18,0xbe,0x1b,
    0xfc,0x56,0x3e,0x4b,0xc6,0xd2,0x79,0x20,0x9a,0xdb,0xc0,0xfe,0x78,0xcd,0x5a,0xf4,
    0x1f,0xdd,0xa8,0x33,0x88,0x07,0xc7,0x31,0xb1,0x12,0x10,0x59,0x27,0x80,0xec,0x5f,
    0x60,0x51,0x7f,0xa9,0x19,0xb5,0x4a,0x0d,0x2d,0xe5,0x7a,0x9f,0x93,0xc9,0x9c,0xef,
    0xa0,0xe0,0x3b,0x4d,0xae,0x2a,0xf5,0xb0,0xc8,0xeb,0xbb,0x3c,0x83,0x53,0x99,0x61,
    0x17,0x2b,0x04,0x7e,0xba,0x77,0xd6,0x26,0xe1,0x69,0x14,0x63,0x55,0x21,0x0c,0x7d,
)

_RCON = (0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36)


def _xtime(a):
    return ((a << 1) ^ 0x11b) & 0xff if a & 0x80 else (a << 1) & 0xff


def _mul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _aes_key_expansion(key: bytes) -> list:
    nk = len(key) // 4  # 8 for AES-256
    nr = nk + 6         # 14 for AES-256
    w = [0] * (4 * (nr + 1))
    for i in range(nk):
        w[i] = int.from_bytes(key[4*i:4*i+4], 'big')

    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1]
        if i % nk == 0:
            # RotWord + SubWord + Rcon
            t = ((t << 8) | (t >> 24)) & 0xffffffff
            t = (_SBOX[(t >> 24) & 0xff] << 24 | _SBOX[(t >> 16) & 0xff] << 16 |
                 _SBOX[(t >> 8) & 0xff] << 8 | _SBOX[t & 0xff])
            t ^= _RCON[i // nk - 1] << 24
        elif nk > 6 and i % nk == 4:
            t = (_SBOX[(t >> 24) & 0xff] << 24 | _SBOX[(t >> 16) & 0xff] << 16 |
                 _SBOX[(t >> 8) & 0xff] << 8 | _SBOX[t & 0xff])
        w[i] = w[i - nk] ^ t
    return w


def _aes_decrypt_block(block: bytes, rk: list) -> bytes:
    nr = len(rk) // 4 - 1  # 14 for AES-256
    s = [0]*16
    for i in range(16):
        s[i] = block[i] ^ ((rk[nr * 4 + i // 4] >> (24 - 8 * (i % 4))) & 0xff)

    for rnd in range(nr - 1, 0, -1):
        # InvShiftRows
        s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
        s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
        s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]
        # InvSubBytes
        s = [_INV_SBOX[b] for b in s]
        # AddRoundKey
        for i in range(16):
            s[i] ^= (rk[rnd * 4 + i // 4] >> (24 - 8 * (i % 4))) & 0xff
        # InvMixColumns
        t = list(s)
        for c in range(4):
            j = c * 4
            s[j]   = _mul(t[j],0x0e) ^ _mul(t[j+1],0x0b) ^ _mul(t[j+2],0x0d) ^ _mul(t[j+3],0x09)
            s[j+1] = _mul(t[j],0x09) ^ _mul(t[j+1],0x0e) ^ _mul(t[j+2],0x0b) ^ _mul(t[j+3],0x0d)
            s[j+2] = _mul(t[j],0x0d) ^ _mul(t[j+1],0x09) ^ _mul(t[j+2],0x0e) ^ _mul(t[j+3],0x0b)
            s[j+3] = _mul(t[j],0x0b) ^ _mul(t[j+1],0x0d) ^ _mul(t[j+2],0x09) ^ _mul(t[j+3],0x0e)

    # Final round (no InvMixColumns)
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]
    s = [_INV_SBOX[b] for b in s]
    for i in range(16):
        s[i] ^= (rk[i // 4] >> (24 - 8 * (i % 4))) & 0xff

    return bytes(s)


def _aes_encrypt_block(block: bytes, rk: list) -> bytes:
    nr = len(rk) // 4 - 1  # 14 for AES-256
    s = list(block)

    # Initial AddRoundKey
    for i in range(16):
        s[i] ^= (rk[i // 4] >> (24 - 8 * (i % 4))) & 0xff

    for rnd in range(1, nr):
        # SubBytes
        s = [_SBOX[b] for b in s]
        # ShiftRows
        s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
        s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
        s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
        # MixColumns
        t = list(s)
        for c in range(4):
            j = c * 4
            s[j] = _mul(t[j], 0x02) ^ _mul(t[j + 1], 0x03) ^ t[j + 2] ^ t[j + 3]
            s[j + 1] = t[j] ^ _mul(t[j + 1], 0x02) ^ _mul(t[j + 2], 0x03) ^ t[j + 3]
            s[j + 2] = t[j] ^ t[j + 1] ^ _mul(t[j + 2], 0x02) ^ _mul(t[j + 3], 0x03)
            s[j + 3] = _mul(t[j], 0x03) ^ t[j + 1] ^ t[j + 2] ^ _mul(t[j + 3], 0x02)
        # AddRoundKey
        for i in range(16):
            s[i] ^= (rk[rnd * 4 + i // 4] >> (24 - 8 * (i % 4))) & 0xff

    # Final round (no MixColumns)
    s = [_SBOX[b] for b in s]
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
    for i in range(16):
        s[i] ^= (rk[nr * 4 + i // 4] >> (24 - 8 * (i % 4))) & 0xff

    return bytes(s)


def _format_orig_number(value: float) -> str:
    """Format numbers like original save text (culture-invariant)."""
    v = float(value)
    if not math.isfinite(v):
        return "0"
    text = format(v, ".17g")
    return text.replace("e", "E")


def _legacy_grid_bounds(sim: Simulation) -> tuple[int, int, int]:
    """Legacy reactor bounds used by original save export."""
    subspace_level = 0
    if len(sim.upgrade_manager.upgrades) > 50:
        try:
            subspace_level = max(0, int(sim.upgrade_manager.upgrades[50].level))
        except (TypeError, ValueError):
            subspace_level = 0
    return (
        _ORIG_BASE_GRID_WIDTH + subspace_level,
        _ORIG_BASE_GRID_HEIGHT + subspace_level,
        _ORIG_GRID_DEPTH,
    )


def _legacy_grid_height_from_upgrades(upgrade_levels: list[int]) -> int:
    """Infer legacy grid height from serialized Subspace Expansion level."""
    subspace_level = 0
    if len(upgrade_levels) > 50:
        try:
            subspace_level = max(0, int(upgrade_levels[50]))
        except (TypeError, ValueError):
            subspace_level = 0
    return _ORIG_BASE_GRID_HEIGHT + subspace_level


def _build_new_export_text(sim: Simulation) -> str:
    """Build unrestricted base64-JSON export payload."""
    data = _build_save_dict(sim)
    json_str = json.dumps(data, separators=(",", ":"))
    return base64.b64encode(json_str.encode("utf-8")).decode("ascii")


def _build_original_export_text(sim: Simulation) -> str | None:
    """Build bounded encrypted Reactor Idle-compatible export text."""
    max_x, max_y, max_z = _legacy_grid_bounds(sim)
    component_entries: list[str] = []
    sorted_components = sorted(sim.components, key=lambda c: (c.grid_z, c.grid_y, c.grid_x))
    for comp in sorted_components:
        type_idx = _ORIG_NAME_TO_INDEX.get(comp.stats.name)
        if type_idx is None:
            print(f"[save] Warning: cannot export unknown component '{comp.stats.name}', skipping")
            continue
        if (
            comp.grid_x < 0
            or comp.grid_y < 0
            or comp.grid_z < 0
            or comp.grid_x >= max_x
            or comp.grid_y >= max_y
            or comp.grid_z >= max_z
        ):
            print(
                "[save] Warning: component "
                f"'{comp.stats.name}' at ({comp.grid_x},{comp.grid_y},{comp.grid_z}) "
                "outside legacy bounds, skipping"
            )
            continue
        # Legacy save coordinates use opposite Y axis from runtime grid coordinates.
        legacy_y = max_y - 1 - comp.grid_y
        component_entries.append(
            ",".join(
                [
                    str(comp.grid_x),
                    str(legacy_y),
                    str(comp.grid_z),
                    str(type_idx),
                    _format_orig_number(comp.heat),
                    _format_orig_number(comp.durability),
                ]
            )
        )
    components_str = ";".join(component_entries)
    if components_str:
        components_str += ";"

    legacy_upgrade_levels = []
    for idx in range(_ORIG_UPGRADE_COUNT):
        if idx < len(sim.upgrade_manager.upgrades):
            legacy_upgrade_levels.append(str(max(0, int(sim.upgrade_manager.upgrades[idx].level))))
        else:
            legacy_upgrade_levels.append("0")
    upgrade_levels = ";".join(legacy_upgrade_levels)
    if upgrade_levels:
        upgrade_levels += ";"

    fields = [
        ("BuildVersion", str(_ORIG_BUILD_VERSION)),
        ("Money", _format_orig_number(sim.store.money)),
        ("Heat", _format_orig_number(sim.reactor_heat)),
        ("Power", _format_orig_number(sim.stored_power)),
        ("ProtiumDepleted", str(max(0, int(sim.depleted_protium_count)))),
        ("TotalHeat", _format_orig_number(sim.store.total_heat_dissipated)),
        ("TotalPower", _format_orig_number(sim.store.total_power_produced)),
        ("HeatThisGame", _format_orig_number(sim.store.heat_dissipated_this_game)),
        ("PowerThisGame", _format_orig_number(sim.store.power_produced_this_game)),
        ("MoneyThisGame", _format_orig_number(sim.store.money_earned_this_game)),
        ("TotalMoney", _format_orig_number(sim.store.total_money)),
        ("CurrentExoticParticles", _format_orig_number(sim.store.exotic_particles)),
        ("TotalExoticParticles", _format_orig_number(sim.store.total_exotic_particles)),
        ("CellsReplace", "1" if sim.replace_mode else "0"),
        ("Paused", "1" if sim.paused else "0"),
        ("Components", components_str),
        ("Upgrades", upgrade_levels),
    ]
    plaintext = "|".join(f"{key}:{value}" for key, value in fields) + "|"
    return _encrypt_original(plaintext)


def _parse_original_save(text: str) -> dict | None:
    """Parse original game pipe-delimited save format into our dict format.

    Format: Key:Value|Key:Value|...|
    Components: x,y,z,typeIndex,heat,durability;...;
    Upgrades: level;level;...;
    """
    fields: dict[str, str] = {}
    for field in text.split("|"):
        if ":" in field:
            key, value = field.split(":", 1)
            fields[key] = value

    if not fields:
        return None

    # Map fields to our internal dict format
    store = {
        "money": float(fields.get("Money", "0")),
        "total_money": float(fields.get("TotalMoney", "0")),
        "money_earned_this_game": float(fields.get("MoneyThisGame", "0")),
        "power": float(fields.get("Power", "0")),
        "total_power_produced": float(fields.get("TotalPower", "0")),
        "power_produced_this_game": float(fields.get("PowerThisGame", "0")),
        "heat": float(fields.get("Heat", "0")),
        "total_heat_dissipated": float(fields.get("TotalHeat", "0")),
        "heat_dissipated_this_game": float(fields.get("HeatThisGame", "0")),
        "exotic_particles": float(fields.get("CurrentExoticParticles", "0")),
        "total_exotic_particles": float(fields.get("TotalExoticParticles", "0")),
    }

    # Parse upgrade levels
    upgrade_levels = []
    upgrades_str = fields.get("Upgrades", "")
    for part in upgrades_str.split(";"):
        part = part.strip()
        if part:
            upgrade_levels.append(int(part))

    # Parse components: x,y,z,typeIndex,heat,durability;
    components = []
    legacy_h = _legacy_grid_height_from_upgrades(upgrade_levels)
    comps_str = fields.get("Components", "")
    for entry in comps_str.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",")
        if len(parts) < 4:
            continue
        x, legacy_y, z, type_idx = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        heat = float(parts[4]) if len(parts) > 4 else 0.0
        durability = float(parts[5]) if len(parts) > 5 else 0.0
        name = _ORIG_INDEX_TO_NAME.get(type_idx)
        if name is None:
            print(f"[save] Warning: unknown original component index {type_idx}")
            continue
        y = legacy_h - 1 - legacy_y if 0 <= legacy_y < legacy_h else legacy_y
        components.append({
            "name": name,
            "x": x, "y": y, "z": z,
            "heat": heat, "durability": durability,
            "depleted": False,
        })

    paused = fields.get("Paused", "0") != "0"
    replace = fields.get("CellsReplace", "1") != "0"

    return {
        "version": 1,
        "store": store,
        "upgrade_levels": upgrade_levels,
        "reactor_heat": float(fields.get("Heat", "0")),
        "stored_power": float(fields.get("Power", "0")),
        "depleted_protium_count": int(float(fields.get("ProtiumDepleted", "0"))),
        "paused": paused,
        "replace_mode": replace,
        "total_ticks": 0,
        "prestige_level": 0,
        "shop_page": 0,
        "selected_component_index": -1,
        "components": components,
    }


def _build_save_dict(sim: Simulation) -> dict:
    """Build a JSON-serializable dict from simulation state."""
    components = []
    for comp in sim.components:
        components.append({
            "name": comp.stats.name,
            "heat": comp.heat,
            "durability": comp.durability,
            "depleted": comp.depleted,
            "x": comp.grid_x,
            "y": comp.grid_y,
            "z": comp.grid_z,
        })

    upgrade_levels = [u.level for u in sim.upgrade_manager.upgrades]

    return {
        "version": 1,
        "store": {
            "money": sim.store.money,
            "total_money": sim.store.total_money,
            "money_earned_this_game": sim.store.money_earned_this_game,
            "power": sim.store.power,
            "total_power_produced": sim.store.total_power_produced,
            "power_produced_this_game": sim.store.power_produced_this_game,
            "heat": sim.store.heat,
            "total_heat_dissipated": sim.store.total_heat_dissipated,
            "heat_dissipated_this_game": sim.store.heat_dissipated_this_game,
            "exotic_particles": sim.store.exotic_particles,
            "total_exotic_particles": sim.store.total_exotic_particles,
        },
        "upgrade_levels": upgrade_levels,
        "reactor_heat": sim.reactor_heat,
        "stored_power": sim.stored_power,
        "depleted_protium_count": sim.depleted_protium_count,
        "paused": sim.paused,
        "replace_mode": sim.replace_mode,
        "total_ticks": sim.total_ticks,
        "prestige_level": sim.prestige_level,
        "shop_page": sim.shop_page,
        "selected_component_index": sim.selected_component_index,
        "components": components,
    }


def _restore_from_dict(sim: Simulation, data: dict) -> bool:
    """Restore simulation state from a save dict. Returns True on success."""
    from game.simulation import ReactorComponent

    try:
        # 1. Restore ResourceStore fields
        store_data = data.get("store", {})
        sim.store.money = float(store_data.get("money", 0.0))
        sim.store.total_money = float(store_data.get("total_money", 0.0))
        sim.store.money_earned_this_game = float(store_data.get("money_earned_this_game", 0.0))
        sim.store.power = float(store_data.get("power", 0.0))
        sim.store.total_power_produced = float(store_data.get("total_power_produced", 0.0))
        sim.store.power_produced_this_game = float(store_data.get("power_produced_this_game", 0.0))
        sim.store.heat = float(store_data.get("heat", 0.0))
        sim.store.total_heat_dissipated = float(store_data.get("total_heat_dissipated", 0.0))
        sim.store.heat_dissipated_this_game = float(store_data.get("heat_dissipated_this_game", 0.0))
        sim.store.exotic_particles = float(store_data.get("exotic_particles", 0.0))
        sim.store.total_exotic_particles = float(store_data.get("total_exotic_particles", 0.0))

        # 2. Restore upgrade levels
        upgrade_levels = data.get("upgrade_levels", [])
        for i, level in enumerate(upgrade_levels):
            if i < len(sim.upgrade_manager.upgrades):
                sim.upgrade_manager.upgrades[i].level = int(level)

        # 2b. Resize grid for Subspace Expansion (upgrade 50) before placing components
        sim.resize_grid_for_subspace()

        # 3. Restore sim scalars
        sim.reactor_heat = float(data.get("reactor_heat", 0.0))
        sim.stored_power = float(data.get("stored_power", 0.0))
        sim.depleted_protium_count = int(data.get("depleted_protium_count", 0))
        sim.paused = bool(data.get("paused", False))
        sim.replace_mode = bool(data.get("replace_mode", True))
        sim.total_ticks = int(data.get("total_ticks", 0))
        sim.prestige_level = int(data.get("prestige_level", 0))
        sim.shop_page = int(data.get("shop_page", 0))
        sim.selected_component_index = int(data.get("selected_component_index", -1))

        # 4. Clear existing grid/components, reconstruct from saved components
        if sim.grid is not None:
            for x, y, z, comp in list(sim.grid.iter_cells()):
                if comp is not None:
                    sim.grid.set(x, y, z, None)
        sim.components.clear()

        # Build name -> stats lookup from shop catalog
        stats_by_name = {comp.name: comp for comp in sim.shop_components}

        for comp_data in data.get("components", []):
            name = comp_data.get("name", "")
            stats = stats_by_name.get(name)
            if stats is None:
                print(f"[save] Warning: unknown component '{name}', skipping")
                continue

            rc = ReactorComponent(
                stats=stats,
                heat=float(comp_data.get("heat", 0.0)),
                durability=float(comp_data.get("durability", 0.0)),
                depleted=bool(comp_data.get("depleted", False)),
                grid_x=int(comp_data.get("x", 0)),
                grid_y=int(comp_data.get("y", 0)),
                grid_z=int(comp_data.get("z", 0)),
            )

            if sim.grid is not None and sim.grid.in_bounds(rc.grid_x, rc.grid_y, rc.grid_z):
                sim.grid.set(rc.grid_x, rc.grid_y, rc.grid_z, rc)
                sim.components.append(rc)
            else:
                print(f"[save] Warning: component '{name}' at ({rc.grid_x},{rc.grid_y}) out of bounds, skipping")

        # 5. Mark pulses dirty and recompute capacities
        sim._pulses_dirty = True
        sim.recompute_max_capacities()

        # 6. Sync store with reactor state
        sim.store.power = sim.stored_power
        sim.store.heat = sim.reactor_heat

        return True

    except (KeyError, TypeError, ValueError) as e:
        print(f"[save] Error restoring save data: {e}")
        return False


def _try_import_data(encoded: str) -> dict | None:
    """Try to parse import data in multiple formats.

    1. Raw JSON dict (desktop auto-save format)
    2. base64 -> JSON dict (new export format)
    3. base64 -> AES-256-CBC ciphertext -> pipe-delimited (original game)
    """
    # 1. Try raw JSON first (desktop save.json / direct paste)
    try:
        data = json.loads(encoded)
        if isinstance(data, dict) and "version" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Try base64 -> JSON (new export format)
    try:
        raw = base64.b64decode(encoded)
    except Exception:
        return None

    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and "version" in data:
            return data
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # 3. Try original game format: base64 -> AES ciphertext -> pipe-delimited
    if len(raw) % 16 == 0 and len(raw) >= 16:
        plaintext = _decrypt_original(raw)
        if plaintext and "|" in plaintext:
            data = _parse_original_save(plaintext)
            if data is not None:
                return data

    return None


# ── Pending import state (used by web file import flow) ──────────────
_pending_import_sim: Simulation | None = None


def _handle_file_import(encoded: str) -> bool:
    """Called from main loop when file content is available (web frame polling)."""
    sim = _pending_import_sim
    if sim is None:
        return False

    encoded = str(encoded).strip()
    data = _try_import_data(encoded)
    if data is None:
        print("[save] Could not parse import file (not a valid save)")
        return False

    return _restore_from_dict(sim, data)


# ══════════════════════════════════════════════════════════════════════
# Platform-specific save/load/export/import
# ══════════════════════════════════════════════════════════════════════

if _WEB:
    _LOCALSTORAGE_KEY = "rev_reactor_save"

    def _bridge_get_save_text() -> str | None:
        try:
            from js import window  # type: ignore
            bridge = getattr(window, "RevReactorHostBridge", None)
            if bridge is not None and hasattr(bridge, "getSaveText"):
                text = bridge.getSaveText()
                if text is None:
                    return None
                return str(text)
        except Exception:
            pass
        return None

    def _bridge_set_save_text(text: str) -> bool:
        try:
            from js import window  # type: ignore
            bridge = getattr(window, "RevReactorHostBridge", None)
            if bridge is not None and hasattr(bridge, "setSaveText"):
                bridge.setSaveText(text)
                return True
        except Exception:
            pass
        return False

    def _bridge_download_text(filename: str, text: str) -> bool:
        try:
            from js import window  # type: ignore
            bridge = getattr(window, "RevReactorHostBridge", None)
            if bridge is not None and hasattr(bridge, "downloadText"):
                bridge.downloadText(filename, text)
                return True
        except Exception:
            pass
        return False

    def save_game(sim: Simulation, path=None) -> None:
        """Auto-save to localStorage."""
        data = _build_save_dict(sim)
        json_str = json.dumps(data, separators=(",", ":"))
        if _bridge_set_save_text(json_str):
            return
        try:
            from js import window  # type: ignore
            window.localStorage.setItem(_LOCALSTORAGE_KEY, json_str)
        except Exception as e:
            print(f"[save] Error saving to localStorage: {e}")

    def load_game(sim: Simulation, path=None) -> bool:
        """Auto-load from localStorage. Returns False on missing/corrupt data."""
        bridge_text = _bridge_get_save_text()
        if bridge_text is not None:
            try:
                data = json.loads(bridge_text)
            except json.JSONDecodeError as e:
                print(f"[save] Error parsing bridged save data: {e}")
                return False
            return _restore_from_dict(sim, data)

        try:
            from js import window  # type: ignore
            text = window.localStorage.getItem(_LOCALSTORAGE_KEY)
            if text is None:
                return False
            # Convert JsProxy string to Python string if needed
            text = str(text)
        except Exception as e:
            print(f"[save] Error reading localStorage: {e}")
            return False
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[save] Error parsing save data: {e}")
            return False
        return _restore_from_dict(sim, data)

    def _download_text(filename: str, text: str) -> None:
        from js import document, Blob, URL  # type: ignore
        blob = Blob.new([text], {"type": "text/plain"})
        url = URL.createObjectURL(blob)
        a = document.createElement("a")
        a.href = url
        a.download = filename
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)

    def export_save_old(sim: Simulation, path=None) -> None:
        """Export bounded original Reactor Idle-compatible encrypted save text."""
        encoded = _build_original_export_text(sim)
        if encoded is None:
            print("[save] Error exporting save: failed to build encrypted export")
            return
        try:
            if not _bridge_download_text("rev_reactor_save_old.txt", encoded):
                _download_text("rev_reactor_save_old.txt", encoded)
        except Exception as e:
            print(f"[save] Error exporting save: {e}")

    def export_save_new(sim: Simulation, path=None) -> None:
        """Export unrestricted new-format base64-JSON save text."""
        encoded = _build_new_export_text(sim)
        try:
            if not _bridge_download_text("rev_reactor_save_new.txt", encoded):
                _download_text("rev_reactor_save_new.txt", encoded)
        except Exception as e:
            print(f"[save] Error exporting new save: {e}")

    def export_save(sim: Simulation, path=None) -> None:
        """Backward-compatible alias for old export."""
        export_save_old(sim, path)

    def import_save_from_file(sim: Simulation) -> bool:
        """Trigger the browser file input element to import a save.

        The actual import happens via _handle_file_import called from the
        frame-based polling system when the file is read.
        """
        global _pending_import_sim
        _pending_import_sim = sim
        try:
            from js import document  # type: ignore
            file_input = document.getElementById("file-input")
            if file_input is not None:
                file_input.value = ""  # Reset so same file can be re-selected
                file_input.click()
        except Exception as e:
            print(f"[save] File input error: {e}")
        return False

else:
    def save_game(sim: Simulation, path: Path) -> None:
        """Auto-save: write JSON atomically (tmp + rename)."""
        data = _build_save_dict(sim)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except OSError as e:
            print(f"[save] Error saving game: {e}")

    def load_game(sim: Simulation, path: Path) -> bool:
        """Auto-load: read JSON, restore state. Returns False on missing/corrupt file."""
        if not path.exists():
            return False
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[save] Error loading save file: {e}")
            return False
        return _restore_from_dict(sim, data)

    def export_save_old(sim: Simulation, path: Path) -> None:
        """Export bounded original Reactor Idle-compatible encrypted save text."""
        encoded = _build_original_export_text(sim)
        if encoded is None:
            print("[save] Error exporting save: failed to build encrypted export")
            return
        try:
            path.write_text(encoded, encoding="utf-8")
        except OSError as e:
            print(f"[save] Error exporting save: {e}")

    def export_save_new(sim: Simulation, path: Path) -> None:
        """Export unrestricted new-format base64-JSON save text."""
        encoded = _build_new_export_text(sim)
        try:
            path.write_text(encoded, encoding="utf-8")
        except OSError as e:
            print(f"[save] Error exporting new save: {e}")

    def export_save(sim: Simulation, path: Path) -> None:
        """Backward-compatible alias for old export."""
        export_save_old(sim, path)

    def import_save_from_file(sim: Simulation) -> bool:
        """Open a file dialog, read the selected file, and import it.

        Supports both our base64-JSON format and the original game's
        encrypted base64 format.
        """
        path = _open_file_dialog()
        if path is None:
            return False

        try:
            encoded = path.read_text(encoding="utf-8-sig").strip()
        except OSError as e:
            print(f"[save] Error reading import file: {e}")
            return False

        data = _try_import_data(encoded)
        if data is None:
            print(f"[save] Could not parse import file (not a valid save)")
            return False

        return _restore_from_dict(sim, data)

    def _open_file_dialog() -> Path | None:
        """Open a native file dialog to select a .txt file. Returns Path or None."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            filepath = filedialog.askopenfilename(
                title="Import Save File",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
            root.destroy()
            if filepath:
                return Path(filepath)
        except Exception as e:
            print(f"[save] File dialog error: {e}")
        return None
