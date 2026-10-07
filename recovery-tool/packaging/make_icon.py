from __future__ import annotations

import argparse
from pathlib import Path
import struct
import zlib


def png(size: int) -> bytes:
    pixels = bytearray()
    radius = size * 0.2
    bolt = (
        (0.54, 0.16),
        (0.30, 0.53),
        (0.48, 0.53),
        (0.42, 0.84),
        (0.71, 0.43),
        (0.52, 0.43),
    )

    def inside_polygon(x: float, y: float) -> bool:
        inside = False
        previous = len(bolt) - 1
        for current, point in enumerate(bolt):
            x1, y1 = bolt[previous]
            x2, y2 = point
            if (y1 > y) != (y2 > y):
                crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                if x < crossing:
                    inside = not inside
            previous = current
        return inside

    for y in range(size):
        pixels.append(0)
        for x in range(size):
            dx = max(size * 0.08 - x, 0, x - size * 0.92)
            dy = max(size * 0.08 - y, 0, y - size * 0.92)
            rounded = dx * dx + dy * dy <= radius * radius
            if not rounded:
                color = (0, 0, 0, 0)
            elif inside_polygon((x + 0.5) / size, (y + 0.5) / size):
                color = (30, 35, 28, 255)
            else:
                color = (157, 210, 67, 255)
            pixels.extend(color)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(
        b"IDAT", zlib.compress(bytes(pixels), 9)
    ) + chunk(b"IEND", b"")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for points in (16, 32, 128, 256, 512):
        (args.output / f"icon_{points}x{points}.png").write_bytes(png(points))
        doubled = points * 2
        (args.output / f"icon_{points}x{points}@2x.png").write_bytes(png(doubled))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
