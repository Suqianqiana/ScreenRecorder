"""Crop the central rounded icon from a generated image and produce icon.png + icon.ico.

Handles two common source types:
- Already-transparent PNG with a central icon (use alpha bbox).
- Generated image with solid background + central icon + possible watermark
  (use background-color subtraction + flood-fill to isolate the icon).

The produced ICO uses BMP-encoded frames for sizes < 256 and a PNG-encoded
frame for 256x256, which is the most compatible format for Windows/PyInstaller.
"""
from __future__ import annotations
import argparse
import io
import os
import struct
import warnings
from PIL import Image, ImageDraw

warnings.filterwarnings("ignore", category=DeprecationWarning)


def _bg_color(img: Image.Image, sample: int = 80) -> tuple[int, int, int]:
    """Average the four corner colours as the margin background reference."""
    r = g = b = n = 0
    for x0, y0 in ((0, 0), (-sample, 0), (0, -sample), (-sample, -sample)):
        x = x0 if x0 >= 0 else img.width + x0
        y = y0 if y0 >= 0 else img.height + y0
        box = (x, y, x + sample, y + sample)
        region = img.crop(box)
        pr, pg, pb, _ = [sum(c.getdata()) for c in region.split()]
        r += pr
        g += pg
        b += pb
        n += sample * sample
    return (r // n, g // n, b // n)


def _color_dist(c1, c2) -> int:
    return sum((a - b) ** 2 for a, b in zip(c1, c2))


def _alpha_bbox(img: Image.Image, threshold: int = 10) -> tuple[int, int, int, int] | None:
    """Return bounding box of pixels whose alpha differs from fully transparent."""
    alpha = img.split()[3]
    # Build a mask of non-transparent-ish pixels
    mask = alpha.point(lambda a: 255 if a > threshold else 0, mode="1")
    return mask.getbbox()


def _foreground_bbox(img: Image.Image, bg: tuple[int, int, int], threshold: int = 35 * 35) -> tuple[int, int, int, int] | None:
    """Return bounding box of central connected foreground region.

    Uses background-color subtraction + flood-fill from the centre so that
    disconnected regions (e.g. corner watermarks) are excluded.
    """
    w, h = img.size
    src_data = list(img.getdata())
    mask_data = [255 if _color_dist(p[:3], bg) > threshold else 0 for p in src_data]
    mask = Image.new("L", (w, h), 0)
    mask.putdata(mask_data)

    cx, cy = w // 2, h // 2
    if mask.getpixel((cx, cy)) == 0:
        threshold = 20 * 20
        mask_data = [255 if _color_dist(p[:3], bg) > threshold else 0 for p in src_data]
        mask.putdata(mask_data)

    ImageDraw.floodfill(mask, (cx, cy), value=128)
    filled_data = [255 if v == 128 else 0 for v in mask.getdata()]
    mask.putdata(filled_data)
    return mask.getbbox()


def _centered_square_crop(img: Image.Image, bbox: tuple[int, int, int, int], padding: float) -> Image.Image:
    x1, y1, x2, y2 = bbox
    w, h = img.size
    side = max(x2 - x1, y2 - y1)
    pad = int(side * padding)
    side += pad * 2
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    x1 = max(0, cx - side // 2)
    y1 = max(0, cy - side // 2)
    x2 = min(w, x1 + side)
    y2 = min(h, y1 + side)
    x1 = max(0, x2 - side)
    y1 = max(0, y2 - side)
    return img.crop((x1, y1, x2, y2))


def _icon_bmp_frame(img: Image.Image) -> bytes:
    """Return DIB + XOR + AND masks for a 32-bit icon frame."""
    w, h = img.size
    pixels = list(img.convert("RGBA").getdata())
    # XOR mask: BGRA, bottom-up
    xor_mask = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = pixels[y * w + x]
            xor_mask.extend([b, g, r, a])
    # AND mask: 1 bit per pixel, 0 = opaque, bottom-up, padded to 4 bytes
    and_row_bytes = ((w + 31) // 32) * 4
    and_mask = bytearray(and_row_bytes * h)
    # DIB header
    dib = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return dib + bytes(xor_mask) + bytes(and_mask)


def _write_ico(frames: list[tuple[int, int, bytes, bool]], path: str) -> None:
    """Write an ICO container.

    Each frame is (width, height, bytes, is_png). For best Windows/PyInstaller
    compatibility, non-256 frames should be BMP-encoded and the 256 frame
    should be PNG-encoded.
    """
    n = len(frames)
    header = struct.pack("<HHH", 0, 1, n)
    entries = b""
    data = b""
    offset = 6 + 16 * n
    for w, h, img_bytes, _ in frames:
        bw = w if w < 256 else 0
        bh = h if h < 256 else 0
        entries += struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32, len(img_bytes), offset)
        data += img_bytes
        offset += len(img_bytes)
    with open(path, "wb") as f:
        f.write(header + entries + data)


def make_icon(src_path: str, out_png_path: str, out_ico_path: str, padding: float = 0.04) -> None:
    img = Image.open(src_path).convert("RGBA")

    # Detect whether the source is already a cropped icon on a transparent canvas.
    # Generated images with solid margins have very few transparent pixels; user-cut
    # icons have lots of transparent pixels but the non-transparent area fills the bbox.
    alpha = img.split()[3]
    alpha_data = list(alpha.getdata())
    opaque_pixels = sum(1 for a in alpha_data if a > 128)
    total_pixels = len(alpha_data)
    alpha_bbox = alpha.getbbox()
    is_already_cropped = (
        opaque_pixels > total_pixels * 0.3
        and alpha_bbox is not None
        and (alpha_bbox[2] - alpha_bbox[0]) >= img.width * 0.95
        and (alpha_bbox[3] - alpha_bbox[1]) >= img.height * 0.95
    )

    if is_already_cropped:
        print("source appears to be a pre-cropped transparent icon; centering on 256x256 canvas")
        icon = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        # Scale up if smaller, keep aspect ratio
        scaled = img.resize((256, 256), Image.LANCZOS)
        icon.paste(scaled, (0, 0), scaled)
    else:
        # Try alpha bbox first; if the source has transparent margins, use it.
        bbox = _alpha_bbox(img)
        if bbox is not None and (bbox[2] - bbox[0]) < img.width * 0.95 and (bbox[3] - bbox[1]) < img.height * 0.95:
            print(f"alpha bbox: {bbox}")
        else:
            print("no usable alpha margin, falling back to background subtraction + flood-fill")
            bg = _bg_color(img)
            bbox = _foreground_bbox(img, bg)
            if bbox is None:
                raise RuntimeError("Could not locate the central icon.")
            print(f"foreground bbox: {bbox}")
        icon = _centered_square_crop(img, bbox, padding).resize((256, 256), Image.LANCZOS)

    icon.save(out_png_path, "PNG")
    print(f"wrote {out_png_path} ({icon.size[0]}x{icon.size[1]})")

    frames: list[tuple[int, int, bytes, bool]] = []
    for s in [16, 24, 32, 48, 64, 128]:
        f = icon.resize((s, s), Image.LANCZOS)
        frames.append((s, s, _icon_bmp_frame(f), False))
    # 256 must be PNG because ICO dimensions are stored in a single byte (0 == 256).
    f256 = icon.resize((256, 256), Image.LANCZOS)
    buf = io.BytesIO()
    f256.save(buf, format="PNG")
    frames.append((256, 256, buf.getvalue(), True))

    _write_ico(frames, out_ico_path)
    print(f"wrote {out_ico_path}")

    with open(out_ico_path, "rb") as fh:
        count = struct.unpack("<HHH", fh.read(6))[2]
    print(f"ico contains {count} frame(s): {[f[0] for f in frames]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crop central rounded icon and generate PNG + multi-size ICO")
    parser.add_argument("source", help="Source image")
    parser.add_argument("png", help="Output PNG path")
    parser.add_argument("ico", help="Output ICO path")
    parser.add_argument("--padding", type=float, default=0.04, help="Padding around the icon")
    args = parser.parse_args()
    make_icon(args.source, args.png, args.ico, args.padding)
