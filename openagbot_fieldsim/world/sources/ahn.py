"""AHN-maaiveldhoogte (DTM 0,5 m) via de WCS van PDOK, in RD (EPSG:28992), NAP-hoogtes.

De GeoTIFF van PDOK is deflate-gecomprimeerd met floating-point predictor (3). tifffile wil daarvoor het pakket
imagecodecs (groot, C); de predictor is eenvoudig, dus die draaien we hier zelf terug met numpy.
"""
from __future__ import annotations

import io
import math
import zlib

import numpy as np

WCS = ("https://service.pdok.nl/rws/ahn/wcs/v1_0?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
       "&COVERAGEID={cov}&SUBSET=x({x0},{x1})&SUBSET=y({y0},{y1})&FORMAT=image/tiff")
NODATA = 3.0e38


def read_float_geotiff(data: bytes) -> tuple[np.ndarray, float, float, float]:
    """(z[rij, kolom] met NaN voor geen data, x_links, y_boven, celgrootte)."""
    import tifffile
    with tifffile.TiffFile(io.BytesIO(data)) as tif:
        p = tif.pages[0]
        h, w = p.shape[:2]
        scale = p.tags["ModelPixelScaleTag"].value
        tie = p.tags["ModelTiepointTag"].value
        x_left, y_top, cell = float(tie[3]), float(tie[4]), float(scale[0])
        if p.compression == 1 and p.predictor == 1:
            z = p.asarray().astype(np.float32)
        else:
            if p.compression not in (8, 32946) or p.is_tiled or p.bitspersample != 32:
                raise ValueError(f"onbekend AHN-formaat: compressie {p.compression}, tiled {p.is_tiled}")
            rows_per = p.rowsperstrip or h
            fh = tif.filehandle
            out = np.empty((h, w), dtype=np.float32)
            row = 0
            for off, n in zip(p.dataoffsets, p.databytecounts):
                fh.seek(off)
                raw = np.frombuffer(zlib.decompress(fh.read(n)), dtype=np.uint8)
                rows = min(rows_per, h - row)
                if p.predictor == 3:
                    b = raw.reshape(rows, w * 4)
                    b = np.cumsum(b, axis=1, dtype=np.uint8)              # horizontaal verschil terug
                    b = b.reshape(rows, 4, w).transpose(0, 2, 1)           # byte-vlakken terug naar samples
                    out[row:row + rows] = np.ascontiguousarray(b).view(">f4").reshape(rows, w)
                else:
                    out[row:row + rows] = raw.view("<f4").reshape(rows, w)
                row += rows
            z = out
    z = np.where(np.abs(z) > NODATA, np.nan, z)
    return z, x_left, y_top, cell


def fetch_dtm(rd_bbox, fetch, cache_dir, cell: float = 0.5, tile_m: float = 500.0, coverage: str = "dtm_05m"):
    """Mozaïek van AHN-tegels over rd_bbox (x0, y0, x1, y1). Geeft (z, x_left, y_top, cell)."""
    x0 = math.floor(rd_bbox[0] / cell) * cell
    y0 = math.floor(rd_bbox[1] / cell) * cell
    x1 = math.ceil(rd_bbox[2] / cell) * cell
    y1 = math.ceil(rd_bbox[3] / cell) * cell
    w, h = int(round((x1 - x0) / cell)), int(round((y1 - y0) / cell))
    z = np.full((h, w), np.nan, dtype=np.float32)
    ty = y1
    while ty > y0 + 1e-6:
        by = max(y0, ty - tile_m)
        tx = x0
        while tx < x1 - 1e-6:
            bx = min(x1, tx + tile_m)
            data = fetch(WCS.format(cov=coverage, x0=tx, x1=bx, y0=by, y1=ty), cache_dir)
            t, tl, tt, tc = read_float_geotiff(data)
            r0 = int(round((y1 - tt) / cell))
            c0 = int(round((tl - x0) / cell))
            rh, cw = min(t.shape[0], h - r0), min(t.shape[1], w - c0)
            z[r0:r0 + rh, c0:c0 + cw] = t[:rh, :cw]
            tx = bx
        ty = by
    return z, x0, y1, cell


def fill_nodata(z: np.ndarray, max_iter: int = 400) -> np.ndarray:
    """Gaten (gebouwen, water) opvullen met het gemiddelde van de buren, van de rand naar binnen."""
    z = z.copy()
    if not np.isnan(z).any():
        return z
    if np.isnan(z).all():
        return np.zeros_like(z)
    for _ in range(max_iter):
        nan = np.isnan(z)
        if not nan.any():
            break
        p = np.pad(z, 1, mode="edge")
        stack = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        cnt = (~np.isnan(stack)).sum(axis=0)
        mean = np.where(cnt > 0, np.nansum(stack, axis=0) / np.maximum(cnt, 1), np.nan)
        z[nan] = mean[nan]
    z[np.isnan(z)] = float(np.nanmean(z))
    return z
