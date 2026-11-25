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


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# icns entries that carry PNG data: (type, pixel size).
ICNS_TYPES = (
    (b"icp4", 16), (b"icp5", 32), (b"ic07", 128), (b"ic08", 256),
    (b"ic09", 512), (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64),
    (b"ic13", 256), (b"ic14", 512),
)


def ico(sizes=ICO_SIZES) -> bytes:
    images = [png(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for size, data in zip(sizes, images):
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


def icns() -> bytes:
    body = b"".join(
        kind + struct.pack(">I", len(data) + 8) + data
        for kind, data in ((kind, png(size)) for kind, size in ICNS_TYPES)
    )
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main() -> int:
    """Write the generic community icon the build scripts expect.

    The official build uses a branded icon that is not part of this kit.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, nargs="?",
                        default=Path(__file__).resolve().parent / "icons")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "recovery-icon.png").write_bytes(png(512))
    (args.output / "recovery-icon.ico").write_bytes(ico())
    (args.output / "recovery-icon.icns").write_bytes(icns())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
