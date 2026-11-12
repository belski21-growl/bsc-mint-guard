"""Fetch one NFT sample image: contract + tokenId -> PNG file (read-only).

Handles, in order:
  - tokenURI via eth_call (selector computed, never memorised)
  - data:application/json;base64 metadata, data:image/* image
  - data:image/svg+xml;base64 -> rendered to PNG via cairosvg (venv)
  - ipfs:// via ipfs.io / pinata / cloudflare-ipfs gateways
  - https:// direct
  - SPA HTML page embedding the image as a data-URI <img> tag
Also returns the raw SVG text when the image is SVG (for filter 2b).

Usage:
    python3 fetch_image.py <contract> <tokenId> <out.png> [--svg-out out.svg]
Exit 0 on success, 1 on fetch failure (technical only, never a verdict).
"""
import base64
import binascii
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rpc import eth_call, sel, padded_addr, checksum, tip

from PIL import Image


def _decode_data_uri(uri):
    m = re.match(r"data:([^;,]+)(;base64)?,(.*)$", uri, re.S)
    if not m:
        return None, None
    mime, b64, data = m.group(1), m.group(2), m.group(3)
    raw = base64.b64decode(data) if b64 else data.encode()
    return mime, raw


def _fetch_url(url):
    import subprocess
    for _ in range(2):
        p = subprocess.run(["curl", "-sS", "--max-time", "30",
                            "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                            url], capture_output=True)
        if p.returncode == 0 and p.stdout:
            return p.stdout
    return None


def _svg_to_png(svg_bytes, out_path):
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=svg_bytes, write_to=str(out_path),
                         output_width=400, output_height=400)
        return True
    except Exception:
        return False


def _image_bytes_to_png(raw, out_path):
    try:
        img = Image.open(io.BytesIO(raw))
        img.convert("RGBA").save(str(out_path))
        return True
    except Exception:
        return False


def resolve_image(meta):
    """metadata dict -> (kind, payload) where kind in svg/raster/url/None."""
    img = meta.get("image") or meta.get("image_url") or ""
    if not img:
        return None, None
    if img.startswith("data:"):
        mime, raw = _decode_data_uri(img)
        if mime and "svg" in mime:
            return "svg", raw
        return "raster", raw
    if img.startswith("ipfs://"):
        cid = img[7:]
        for gw in ("https://ipfs.io/ipfs/", "https://gateway.pinata.cloud/ipfs/",
                   "https://cloudflare-ipfs.com/ipfs/"):
            raw = _fetch_url(gw + cid)
            if raw:
                return ("svg", raw) if raw.lstrip().startswith(b"<svg") else ("raster", raw)
        return None, None
    if img.startswith("http"):
        raw = _fetch_url(img)
        if not raw:
            return None, None
        if raw.lstrip().startswith(b"<svg"):
            return "svg", raw
        if b"<html" in raw[:500].lower() or b"<!doctype" in raw[:500].lower():
            # SPA page embedding the image as data-URI <img>
            m = re.search(rb'data:image/(?:webp|png|jpeg);base64,([A-Za-z0-9+/=]+)', raw)
            if m:
                try:
                    return "raster", base64.b64decode(m.group(1))
                except binascii.Error:
                    pass
            return None, None
        return "raster", raw
    return None, None


def fetch(contract, token_id, out_png, svg_out=None):
    contract = checksum(contract)
    data = sel("tokenURI(uint256)") + format(int(token_id), "064x")
    uri = eth_call(contract, data)
    if uri == "0x":
        raise RuntimeError("tokenURI returned empty")
    # ABI-decode string return
    ln = int(uri[66:130], 16)
    raw_uri = bytes.fromhex(uri[130:130 + ln * 2]).decode(errors="replace")
    if raw_uri.startswith("data:"):
        mime, raw = _decode_data_uri(raw_uri)
        meta = json.loads(raw.decode())
    elif raw_uri.startswith("http") or raw_uri.startswith("ipfs://"):
        url = raw_uri
        if url.startswith("ipfs://"):
            url = "https://ipfs.io/ipfs/" + url[7:]
        blob = _fetch_url(url)
        if not blob:
            raise RuntimeError("metadata fetch failed")
        try:
            meta = json.loads(blob.decode())
        except Exception:
            # SPA fallback: the metadata route sometimes serves the SPA HTML
            # with the card image embedded as a data-URI <img>.
            # Extract it directly.
            m = re.search(rb'data:image/(?:webp|png|jpeg);base64,([A-Za-z0-9+/=]+)', blob)
            if not m:
                raise RuntimeError("metadata is HTML without an embedded image")
            try:
                raw_img = base64.b64decode(m.group(1))
            except binascii.Error:
                raise RuntimeError("embedded image base64 undecodable")
            out_png = Path(out_png)
            if not _image_bytes_to_png(raw_img, out_png):
                raise RuntimeError("embedded image decode failed")
            return "raster"
    else:
        raise RuntimeError(f"unhandled tokenURI scheme: {raw_uri[:60]!r}")
    kind, payload = resolve_image(meta)
    if kind is None:
        raise RuntimeError("image could not be resolved from metadata")
    out_png = Path(out_png)
    if kind == "svg":
        if svg_out:
            Path(svg_out).write_bytes(payload)
        if not _svg_to_png(payload, out_png):
            raise RuntimeError("SVG render failed")
    else:
        if not _image_bytes_to_png(payload, out_png):
            raise RuntimeError("raster decode failed")
    return kind


def main():
    if len(sys.argv) < 4:
        print("usage: fetch_image.py <contract> <tokenId> <out.png> [--svg-out out.svg]", file=sys.stderr)
        return 2
    svg_out = None
    if "--svg-out" in sys.argv:
        svg_out = sys.argv[sys.argv.index("--svg-out") + 1]
    try:
        kind = fetch(sys.argv[1], sys.argv[2], sys.argv[3], svg_out)
        print(f"OK kind={kind} -> {sys.argv[3]}" + (f" svg={svg_out}" if svg_out else ""))
        return 0
    except Exception as e:  # noqa: BLE001 - fetch failure is technical only
        print(f"FETCH FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
