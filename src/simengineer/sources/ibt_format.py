"""Lecture/écriture du format de télémétrie disque iRacing (.ibt).

Structure supposée (docs/ASSUMPTIONS.md, section C — à vérifier sur un vrai fichier) :
en-tête irsdk (112 octets), sous-en-tête disque (32 octets), en-têtes de variables
(144 octets chacun), YAML de session (cp1252), puis `recordCount` lignes de `bufLen` octets.
L'écrivain sert aux tests (aller-retour) et à exporter une session synthétique.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np

from simengineer.core.vars import VARS
from simengineer.sources.base import RawSession
from simengineer.sources.session_yaml import parse_session_yaml, session_info_to_yaml

# ver, status, tickRate, infoUpdate, infoLen, infoOff, numVars, varOff, numBuf, bufLen, pad[2]
_HEADER = struct.Struct("<10i2i")
_VARBUF = struct.Struct("<2i2i")  # tickCount, bufOffset, pad
_DISK = struct.Struct("<qddii")  # startDate, startTime, endTime, lapCount, recordCount
_VARHDR = struct.Struct("<iii?3x32s64s32s")
HEADER_SIZE = _HEADER.size + 4 * _VARBUF.size  # 112

_TYPES: dict[int, tuple[str, str]] = {
    0: ("S1", "char"),
    1: ("?", "bool"),
    2: ("<i4", "int"),
    3: ("<u4", "bitfield"),
    4: ("<f4", "float"),
    5: ("<f8", "double"),
}


class IbtFormatError(ValueError):
    pass


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("cp1252", errors="replace")


def read_ibt(path: Path) -> RawSession:
    data = np.memmap(path, dtype=np.uint8, mode="r")
    if data.size < HEADER_SIZE + _DISK.size:
        raise IbtFormatError(f"{path} : fichier trop court pour un .ibt")
    head = _HEADER.unpack_from(data, 0)
    tick_rate, info_len, info_off, num_vars, var_off, buf_len = (
        head[2],
        head[4],
        head[5],
        head[6],
        head[7],
        head[9],
    )
    _tick, buf_off, _p1, _p2 = _VARBUF.unpack_from(data, _HEADER.size)
    _date, _t0, _t1, _laps, n_rec = _DISK.unpack_from(data, HEADER_SIZE)
    if not (0 < num_vars < 4096 and 0 < buf_len < 1 << 20 and 0 < tick_rate <= 360):
        raise IbtFormatError(f"{path} : en-tête .ibt incohérent")
    available = (data.size - buf_off) // buf_len
    n_rec = available if n_rec <= 0 else min(n_rec, available)

    names, formats, offsets = [], [], []
    meta: dict[str, tuple[str, str]] = {}
    for i in range(num_vars):
        vtype, offset, count, _as_time, name_b, desc_b, unit_b = _VARHDR.unpack_from(
            data, var_off + i * _VARHDR.size
        )
        if vtype not in _TYPES:
            raise IbtFormatError(f"{path} : type de variable inconnu {vtype}")
        name = _cstr(name_b)
        fmt = _TYPES[vtype][0]
        names.append(name)
        formats.append((fmt, (count,)) if count > 1 else fmt)
        offsets.append(offset)
        meta[name] = (_cstr(unit_b), _cstr(desc_b))
    dtype = np.dtype({"names": names, "formats": formats, "offsets": offsets, "itemsize": buf_len})
    table = np.frombuffer(data, dtype=dtype, count=n_rec, offset=buf_off)
    columns: dict[str, np.ndarray[Any, Any]] = {}
    for name in names:
        col = np.ascontiguousarray(table[name])
        if col.dtype.kind == "f" and col.dtype.itemsize == 4:
            col = col.astype(np.float64)
        elif col.dtype.kind == "i":
            col = col.astype(np.int64)
        columns[name] = col
    yaml_text = bytes(data[info_off : info_off + info_len]).decode("cp1252", errors="replace")
    session_num = 0
    num_name = VARS["session_num"].name
    if num_name in columns and n_rec:
        session_num = int(columns[num_name][n_rec // 2])
    info = parse_session_yaml(yaml_text.rstrip("\0"), session_num)
    return RawSession(info=info, columns=columns, tick_rate=int(tick_rate), var_meta=meta)


def write_ibt(raw: RawSession, path: Path) -> None:
    """Écrit une `RawSession` au format .ibt supposé."""
    yaml_bytes = session_info_to_yaml(raw.info).encode("cp1252") + b"\0"
    specs: list[tuple[str, int, int, np.ndarray[Any, Any]]] = []  # nom, type, count, données
    double_names = {VARS[k].name for k in ("session_time", "session_time_remain", "lat", "lon")}
    for name, col in raw.columns.items():
        kind = col.dtype.kind
        if kind == "f":
            vtype = 5 if name in double_names else 4
        elif kind == "b":
            vtype = 1
        elif kind == "u":
            vtype = 3
        else:
            vtype = 2
        count = 1 if col.ndim == 1 else int(col.shape[1])
        specs.append((name, vtype, count, col))
    offsets, pos = [], 0
    for _name, vtype, count, _col in specs:
        offsets.append(pos)
        pos += np.dtype(_TYPES[vtype][0]).itemsize * count
    buf_len = pos
    var_off = HEADER_SIZE + _DISK.size
    info_off = var_off + len(specs) * _VARHDR.size
    buf_off = info_off + len(yaml_bytes)
    n = raw.n_frames
    dtype = np.dtype(
        {
            "names": [s[0] for s in specs],
            "formats": [(_TYPES[s[1]][0], (s[2],)) if s[2] > 1 else _TYPES[s[1]][0] for s in specs],
            "offsets": offsets,
            "itemsize": buf_len,
        }
    )
    table = np.zeros(n, dtype=dtype)
    for name, _vtype, _count, col in specs:
        table[name] = col
    duration = n / raw.tick_rate
    with path.open("wb") as fh:
        fh.write(
            _HEADER.pack(
                2,
                1,
                raw.tick_rate,
                0,
                len(yaml_bytes),
                info_off,
                len(specs),
                var_off,
                1,
                buf_len,
                0,
                0,
            )
        )
        fh.write(_VARBUF.pack(n, buf_off, 0, 0))
        fh.write(b"\0" * (3 * _VARBUF.size))
        fh.write(_DISK.pack(0, 0.0, duration, 0, n))
        for (name, vtype, count, _col), offset in zip(specs, offsets, strict=True):
            unit, desc = raw.var_meta.get(name, ("", ""))
            fh.write(
                _VARHDR.pack(
                    vtype,
                    offset,
                    count,
                    False,
                    name.encode("cp1252"),
                    desc.encode("cp1252"),
                    unit.encode("cp1252"),
                )
            )
        fh.write(yaml_bytes)
        fh.write(table.tobytes())
