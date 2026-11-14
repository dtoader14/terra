"""Quality-filtered nearest-neighbour display grid, never spatial gap filling."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from nasa import NasaError

PALETTES = {
    "thermal": ["#293e83", "#397cb8", "#62c6c1", "#f3df83", "#ed9555", "#b9384c"],
    "vegetation": ["#865c46", "#c5a96e", "#e8df9c", "#a2c888", "#4a9e73", "#126253"],
    "water": ["#f6f1df", "#cfe6cf", "#8fd0c4", "#4aa7c7", "#2f6fb0", "#27408b"],
}


def _temperature(quality):
    qa = np.nan_to_num(quality, nan=255).astype(np.uint16)
    return ((qa & 3) == 0) & (((qa >> 2) & 3) == 0) & (((qa >> 6) & 3) == 0)


def _good_bit(quality):
    return (np.nan_to_num(quality, nan=255).astype(np.uint16) & 1) == 0  # the quality layer's own fill is 255


# Which pixels are drawn, by the quality layer's kind: (the test, how the metadata words it).
# NASA's tables for each layer: AppEEARS /api/quality/<product>/<quality layer>.
QUALITY = {
    "temperature": (_temperature, "mandatory QA=0, data quality=0, LST error bits=0 (<=1 K)"),  # MOD11, MYD11
    "reliability": (lambda quality: quality == 0, "pixel reliability=0 (good data)"),            # MOD13, MYD13
    "good_bit": (_good_bit, "bit 0=0 (good quality, main algorithm)"),                           # MOD15, MOD16, MOD17
}


def file_key(name):
    match = re.search(r"_(doy\d{7,13}|\d{8}T\d{6})_(.+)\.tiff?$", name, re.I)
    if not match:
        raise NasaError("Unrecognized NASA raster filename; cannot safely match its quality layer.")
    return match.group(1), match.group(2)


def date_label(key):
    if key.startswith("doy"):
        return (datetime(int(key[3:7]), 1, 1) + timedelta(days=int(key[7:10]) - 1)).date().isoformat()
    return datetime.strptime(key[:8], "%Y%m%d").date().isoformat()


def grid_spec(bbox, size=640):
    bounds = transform_bounds("EPSG:4326", "EPSG:3857", *bbox)
    ratio = (bounds[2] - bounds[0]) / (bounds[3] - bounds[1])
    width, height = (size, max(1, round(size / ratio))) if ratio >= 1 else (max(1, round(size * ratio)), size)
    return dict(crs="EPSG:3857", transform=from_bounds(*bounds, width, height), width=width, height=height)


def read_raw(path, grid):
    with rasterio.open(path) as src:
        if not src.crs:
            raise NasaError("NASA raster has no coordinate reference system.")
        # Explicit float destination avoids treating QA=0 (a GOOD pixel) as fill.
        # Science fill and valid ranges are applied separately below.
        with WarpedVRT(src, **grid, resampling=Resampling.nearest, dtype="float32", src_nodata=None, nodata=float("nan")) as vrt:
            raw = vrt.read(1)
        return raw, src.scales[0], src.offsets[0], src.nodata


def unpack_and_mask(raw, quality, metadata, variable, scale=1, offset=0, nodata=None):
    valid = np.isfinite(raw) & np.isfinite(quality)
    if nodata is not None:
        valid &= raw != nodata
    for fill in metadata.get("FillValueAll", [metadata.get("FillValue")]):
        if fill is not None:
            valid &= raw != float(fill)
    # AppEEARS area outputs retain packed values. Rasterio read does NOT apply
    # band scale/offset. Prefer file metadata and fall back to NASA's catalog.
    if scale == 1 and offset == 0:
        scale = float(metadata.get("ScaleFactor") or 1)
        offset = float(metadata.get("AddOffset") or 0)
    for key, op in (("OrigValidMin", np.greater_equal), ("OrigValidMax", np.less_equal)):
        if metadata.get(key) not in (None, ""):
            valid &= op(raw, float(metadata[key]))
    valid &= QUALITY[variable["quality"]][0](quality)
    result = raw.astype(np.float32) * scale + offset
    if variable["unit"] == "°C":
        result -= 273.15
    return np.where(valid, result, np.nan).astype(np.float32)


def temporal_mean(frames):
    stack = np.asarray(frames)
    counts = np.isfinite(stack).sum(axis=0)
    result = np.full(stack.shape[1:], np.nan, dtype=np.float32)
    np.divide(np.nansum(stack, axis=0), counts, out=result, where=counts > 0)
    return result, counts.astype(np.uint16)


def statistics(array):
    values = array[np.isfinite(array)]
    return dict(min=float(values.min()), max=float(values.max()), mean=float(values.mean()), valid_pixels=int(values.size), coverage=round(100 * values.size / array.size, 1)) if values.size else dict(min=None, max=None, mean=None, valid_pixels=0, coverage=0)


def colorize(array, low, high, palette):
    stops = np.array([[int(color[i:i+2], 16) for i in (1, 3, 5)] for color in PALETTES[palette]])
    normalized = np.clip((np.nan_to_num(array, nan=low) - low) / max(high - low, 1e-8), 0, 1)
    image = np.zeros((*array.shape, 4), dtype=np.uint8)
    for channel in range(3):
        image[:, :, channel] = np.interp(normalized, np.linspace(0, 1, len(stops)), stops[:, channel]).astype(np.uint8)
    image[:, :, 3] = np.isfinite(array).astype(np.uint8) * 255
    return Image.fromarray(image)


def process_bundle(client, job, directory, update):
    variable, bbox = job["variable_info"], job["query"]["bbox"]
    catalog = client.request("GET", f"/product/{variable['product']}")
    metadata = catalog[variable["layer"]]
    bundle = client.request("GET", f"/bundle/{job['task_id']}")
    files = bundle.get("files", [])
    science, quality = {}, {}
    for item in files:
        name = Path(item["file_name"]).name
        if not name.lower().endswith((".tif", ".tiff")):
            continue
        if f"{variable['product']}_{variable['layer']}_" in name:
            science[file_key(name)] = item
        elif f"{variable['product']}_{variable['qa']}_" in name:
            quality[file_key(name)] = item
    if not science:
        raise NasaError("NASA returned no rasters for this region and period. Try another date range or location.")
    if len(science) > 24:
        raise NasaError("Too many source rasters for the local preview. Select a shorter period.")
    if any(key not in quality for key in science):
        raise NasaError("NASA did not return matching quality rasters. The app will not display unverified pixels.")
    needed = list(science.values()) + [quality[key] for key in science]
    if sum(item.get("file_size", 0) for item in needed) > 500_000_000:
        raise NasaError("This result exceeds the local 500 MB limit. Select a smaller region.")
    grid = grid_spec(bbox)
    frames, labels, sources = [], [], []
    for index, key in enumerate(sorted(science)):
        update(f"Downloading and checking composite {index + 1} of {len(science)}")
        source_path = client.download(job["task_id"], science[key], directory)
        qa_path = client.download(job["task_id"], quality[key], directory)
        raw, scale, offset, nodata = read_raw(source_path, grid)
        qa, _, _, _ = read_raw(qa_path, grid)
        frames.append(unpack_and_mask(raw, qa, metadata, variable, scale, offset, nodata))
        labels.append(date_label(key[0]))
        sources.append({"date": labels[-1], "science": science[key], "quality": quality[key]})
        source_path.unlink(missing_ok=True)
        qa_path.unlink(missing_ok=True)
    mean, counts = temporal_mean(frames)
    if not np.isfinite(mean).any():
        raise NasaError("No good-quality pixels remain after cloud and quality filtering. Try a longer period or another region.")
    all_stats = [statistics(frame) for frame in frames]
    low = min(row["min"] for row in all_stats if row["min"] is not None)
    high = max(row["max"] for row in all_stats if row["max"] is not None)
    if low == high:
        low, high = low - 0.5, high + 0.5
    colorize(mean, low, high, variable["palette"]).save(directory / "mean.png")
    frame_info = []
    for i, (label, frame, stats) in enumerate(zip(labels, frames, all_stats)):
        filename = f"frame-{i}.png"
        colorize(frame, low, high, variable["palette"]).save(directory / filename)
        frame_info.append({"date": label, "file": filename, "stats": stats})
    with rasterio.open(directory / "mean.tif", "w", driver="GTiff", count=1, dtype="float32", nodata=float("nan"), compress="deflate", **grid) as dst:
        dst.write(mean, 1)
        dst.set_band_description(1, f"{variable['label']} ({variable['unit']}); unweighted mean of available good-quality composites")
    with rasterio.open(directory / "count.tif", "w", driver="GTiff", count=1, dtype="uint16", nodata=0, compress="deflate", **grid) as dst:
        dst.write(counts, 1)
    result = {
        "provider": "NASA AppEEARS", "task_id": job["task_id"], "query": job["query"], "variable": variable,
        "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]], "stats": statistics(mean),
        "legend": {"min": low, "max": high, "colors": PALETTES[variable["palette"]]},
        "frames": frame_info, "sources": sources, "layer_metadata": metadata,
        "grid": {"crs": "EPSG:3857", "width": grid["width"], "height": grid["height"]},
        "method": "Nearest-neighbour sampling onto a display grid of at most 640 pixels per side. Arithmetic mean of available good-quality source composites, equally weighted; no interpolation or gap filling. Composite dates are start dates and may represent observations outside the requested endpoints. Statistics describe the display grid, not an area-weighted regional estimate.",
        "quality": f"{variable['product']} {variable['qa']}: {QUALITY[variable['quality']][1]}. Fill and out-of-range values excluded.",
    }
    (directory / "metadata.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
