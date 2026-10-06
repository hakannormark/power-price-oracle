"""Build the static geographic layers for the BESS screening map.

Run by hand when a source updates (Ei concessions roughly every six months, OSM
when needed). Needs the optional geo dependencies, which the hourly pipeline
does not install:

    pip install -r requirements-geo.txt
    python -m src.geo.build_static            # downloads, then writes site/data/bess-map/

Database-style geometry work happens in SWEREF99 TM (EPSG:3006); everything
written for the web map is WGS84 (EPSG:4326). Every layer carries `source`,
`retrieved_at` and `valid_from` in meta.json.

What this deliberately does NOT produce: free capacity per station, a claim that
a site "can be connected", or a grid model. OSM substations are a proximity proxy
for higher voltage and are incomplete, especially below 130 kV.
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import shapefile  # pyshp
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import transform, unary_union

from ..config import ROOT
from ..timeutil import TZ, iso

log = logging.getLogger(__name__)

OUT_DIR = ROOT / "site" / "data" / "bess-map"
CACHE_DIR = ROOT / "data" / "raw" / "geo"
UA = {"User-Agent": "power-price-oracle/1.0 (bess-map; github.com/hakannormark/power-price-oracle)"}

EI_LOCAL_ZIP = (
    "https://ei.se/download/18.494d10561a01f4a16856a9a/1787564897631/"
    "N%C3%A4tkoncession-f%C3%B6r-omr%C3%A5de-lokaln%C3%A4t.zip"
)
EI_PAGE = "https://ei.se/bransch/koncessioner/ansokan-natkoncession-for-omrade"
ZONES_URL = "https://raw.githubusercontent.com/electricitymaps/electricitymaps-contrib/master/geo/world.geojson"
OVERPASS = "https://overpass-api.de/api/interpreter"
NVR_WFS = "https://geodata.naturvardsverket.se/naturvardsregistret/wfs"

CELL_M = 10_000  # 10 x 10 km analysis cells
TO_3006 = Transformer.from_crs(4326, 3006, always_xy=True).transform
TO_4326 = Transformer.from_crs(3006, 4326, always_xy=True).transform

# Protection types that make a site practically unbuildable for a BESS. Water
# protection areas are restrictive but not exclusionary, so they are reported apart.
HARD_PROTECTION = {
    "Nationalpark",
    "Naturreservat",
    "Naturvårdsområde",
    "Kulturreservat",
    "Biotopskyddsområde",
    "Djurskyddsområde",
    "Växtskyddsområde",
    "Djur- och växtskyddsområde",
}

# Local flexibility markets. Hand-compiled; coverage is a centre + radius, not the
# real network boundary. Source: Power Circle, Flexability delrapport 2 (okt 2025),
# NODES / Effekthandel Väst season reports. Verify with the DSO before use.
FLEX_MARKETS = [
    {"id": "ehv", "name": "Effekthandel Väst", "area": "Göteborg", "lat": 57.71, "lon": 11.97, "radius_km": 25,
     "operator": "Göteborg Energi m.fl. (NODES)",
     "note": "Säsong 2025/26: ca 70 MW tillgängligt, 1 209 MWh upphandlat. Produkter LongFlex, ShortFlex, MaxUsage. Minsta bud 0,05 MW.",
     "url": "https://nodesmarket.com/effekthandel-vast/"},
    {"id": "sthlmflex", "name": "sthlmflex", "area": "Stockholm", "lat": 59.33, "lon": 18.07, "radius_km": 35,
     "operator": "Ellevio, Vattenfall Eldistribution, Svenska kraftnät", "note": "Lokal flexmarknad för Stockholmsregionen.",
     "url": "https://www.svk.se/"},
    {"id": "uppsala", "name": "Flexmarknad Uppsala", "area": "Uppsala", "lat": 59.86, "lon": 17.64, "radius_km": 18,
     "operator": "Vattenfall Eldistribution (ursprung CoordiNet)", "note": "Effektbrist i regionnätet, upphandlas säsongsvis.",
     "url": "https://www.vattenfalleldistribution.se/"},
    {"id": "jamtflex", "name": "JämtFlex", "area": "Östersund", "lat": 63.18, "lon": 14.64, "radius_km": 25,
     "operator": "Jämtkraft Elnät", "note": "Lokal flexmarknad i Jämtland.", "url": "https://www.jamtkraft.se/"},
    {"id": "eon-skane", "name": "E.ON lokal flexmarknad", "area": "Södra Skåne", "lat": 55.60, "lon": 13.10, "radius_km": 35,
     "operator": "E.ON Energidistribution (NODES)",
     "note": "En säsong: 52 aktiverade timmar av 496 tillgänglighetstimmar.", "url": "https://www.eon.se/"},
    {"id": "hassleholm", "name": "Hässleholm", "area": "Hässleholm", "lat": 56.16, "lon": 13.77, "radius_km": 12,
     "operator": "Hässleholm Miljö / E.ON", "note": "Lägre aktiveringspriser än övriga marknader.", "url": "https://www.eon.se/"},
    {"id": "kinnekulle", "name": "Kinnekulle", "area": "Götene", "lat": 58.53, "lon": 13.45, "radius_km": 12,
     "operator": "Kinnekulle Energi", "note": "Liten lokal marknad.", "url": "https://www.kinnekulleenergi.se/"},
]
FLEX_SOURCE = "Power Circle, Flexability delrapport 2 (okt 2025); NODES/Effekthandel Väst säsongsrapporter"


# ------------------------------------------------------------------- download
def _fetch(url: str, name: str, data: bytes | None = None, timeout: int = 300, refresh: bool = False) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and not refresh:
        return path.read_bytes()
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read()
    path.write_bytes(body)
    return body


def _retrieved(name: str) -> str:
    path = CACHE_DIR / name
    return iso(datetime.fromtimestamp(path.stat().st_mtime, TZ))


def _round_coords(obj: Any, nd: int = 4) -> Any:
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(obj[0], nd), round(obj[1], nd)]
        return [_round_coords(o, nd) for o in obj]
    return obj


def _web_geom(geom_3006, tol: float) -> dict:
    g = geom_3006.simplify(tol, preserve_topology=True)
    g = transform(TO_4326, g)
    m = mapping(g)
    return {"type": m["type"], "coordinates": _round_coords(m["coordinates"])}


# ------------------------------------------------------------------- layers
def load_zones() -> dict[str, Any]:
    world = json.loads(_fetch(ZONES_URL, "electricitymaps_world.geojson"))
    zones = {}
    for feat in world["features"]:
        props = feat.get("properties", {})
        name = props.get("zoneName") or props.get("zone_name") or props.get("id")
        if name in ("SE-SE1", "SE-SE2", "SE-SE3", "SE-SE4"):
            zones[name[-3:]] = transform(TO_3006, shape(feat["geometry"])).buffer(0)
    if len(zones) != 4:
        raise RuntimeError(f"Expected 4 Swedish zones, found {sorted(zones)}")
    return zones


def load_concessions() -> list[dict[str, Any]]:
    raw = _fetch(EI_LOCAL_ZIP, "ei_lokalnat.zip")
    zf = zipfile.ZipFile(io.BytesIO(raw))
    base = next(n[:-4] for n in zf.namelist() if n.endswith(".shp"))
    reader = shapefile.Reader(
        shp=io.BytesIO(zf.read(base + ".shp")),
        shx=io.BytesIO(zf.read(base + ".shx")),
        dbf=io.BytesIO(zf.read(base + ".dbf")),
        encoding="utf-8",
    )
    out = []
    for sr in reader.iterShapeRecords():
        rec = sr.record.as_dict()
        geom = shape(sr.shape.__geo_interface__).buffer(0)
        if geom.is_empty:
            continue
        out.append({
            "id": str(rec.get("KONCESSION") or ""),
            "owner": (rec.get("FöretagNa") or "").strip(),
            "unit": rec.get("Enhet"),
            "kv": rec.get("Spanning"),
            "geom": geom,
        })
    return out


def _voltage_max(tag: str | None) -> int | None:
    if not tag:
        return None
    vals = [int(v) for v in re.findall(r"\d{4,7}", tag)]
    return max(vals) if vals else None


OVERPASS_MIRRORS = (
    OVERPASS,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


def _overpass(query: str, name: str) -> bytes:
    import time

    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt in range(6):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            return _fetch(url, name, data=body)
        except Exception as exc:  # noqa: BLE001 - busy public servers, try the next
            last = exc
            log.warning("Overpass %s failed (%s), retrying", url, exc)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass unavailable: {last}")


def load_osm() -> tuple[list[dict], list[dict], str]:
    area = 'area["ISO3166-1"="SE"][admin_level=2]->.a;'
    q_sub = f'[out:json][timeout:240];{area}nwr["power"="substation"](area.a);out center tags;'
    q_line = f'[out:json][timeout:240];{area}way["power"="line"]["voltage"~"[1-4][0-9][0-9]000"](area.a);out geom tags;'
    subs_raw = json.loads(_overpass(q_sub, "osm_substations.json"))
    lines_raw = json.loads(_overpass(q_line, "osm_lines.json"))
    osm_ts = subs_raw.get("osm3s", {}).get("timestamp_osm_base", "")

    subs = []
    for el in subs_raw.get("elements", []):
        tags = el.get("tags", {})
        v = _voltage_max(tags.get("voltage"))
        if v is None or v < 40000:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None:
            continue
        x, y = TO_3006(lon, lat)
        subs.append({
            "name": tags.get("name") or tags.get("ref") or "",
            "operator": tags.get("operator") or "",
            "kv": round(v / 1000),
            "lat": round(lat, 4), "lon": round(lon, 4),
            "pt": Point(x, y),
        })

    lines = []
    for el in lines_raw.get("elements", []):
        tags = el.get("tags", {})
        v = _voltage_max(tags.get("voltage"))
        geom = el.get("geometry")
        if not v or v < 130000 or not geom or len(geom) < 2:
            continue
        ls = LineString([TO_3006(p["lon"], p["lat"]) for p in geom])
        lines.append({"kv": round(v / 1000), "operator": tags.get("operator") or "", "geom": ls})
    return subs, lines, osm_ts


def load_protected() -> list[dict[str, Any]]:
    """Naturvårdsregistret, protected areas, GML 3.2 in EPSG:3006 (axis order N, E)."""
    ns = {"gml": "http://www.opengis.net/gml/3.2", "n": "https://geodata.naturvardsverket.se/naturvardsregistret/wfs"}
    out: list[dict[str, Any]] = []
    # 500 is the server's own page size. Past index 1000 it ignores `count` and
    # returns everything that is left in one large response, which is fine.
    start, page = 0, 500
    while True:
        params = urllib.parse.urlencode({
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "Naturvardsregistret_WFS:SkyddadeOmraden", "count": page, "startIndex": start,
        })
        # The service sometimes answers with a header that promises features and a
        # body that holds none. Trust the count in the header and ask again.
        for attempt in range(5):
            body = _fetch(f"{NVR_WFS}?{params}", f"nvr_{start:06d}.gml", refresh=attempt > 0)
            root = ET.fromstring(body)
            members = root.findall("{http://www.opengis.net/wfs/2.0}member")
            if len(members) >= int(root.get("numberReturned") or len(members)):
                break
            log.warning("NVR page at %s truncated (%s of %s), retrying", start, len(members), root.get("numberReturned"))
        else:
            raise RuntimeError(f"Naturvårdsregistret page at {start} kept coming back truncated")
        matched = int(root.get("numberMatched") or 0)
        for m in members:
            feat = m[0]
            typ = (feat.findtext("n:SKYDDSTYP", default="", namespaces=ns) or "").strip()
            name = (feat.findtext("n:NAMN", default="", namespaces=ns) or "").strip()
            polys = []
            for poly in feat.iter("{http://www.opengis.net/gml/3.2}Polygon"):
                rings = []
                for ring_parent in ("exterior", "interior"):
                    for rp in poly.findall(f"gml:{ring_parent}", ns):
                        pl = rp.find(".//gml:posList", ns)
                        if pl is None or not pl.text:
                            continue
                        nums = [float(v) for v in pl.text.split()]
                        coords = [(nums[i + 1], nums[i]) for i in range(0, len(nums) - 1, 2)]  # (E, N)
                        rings.append(coords)
                if rings and len(rings[0]) >= 4:
                    polys.append(Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4]))
            if polys:
                geom = MultiPolygon(polys).buffer(0) if len(polys) > 1 else polys[0].buffer(0)
                out.append({"type": typ, "name": name, "geom": geom})
        log.info("NVR page at %s: %s features", start, len(members))
        # The server caps a page below the requested count (500 at the time of
        # writing), so a short page is not the last page. Only an empty one is.
        start += len(members)
        if not members or (matched and start >= matched):
            break
    if matched and start < matched:
        raise RuntimeError(f"Naturvårdsregistret: read {start} of {matched} features")
    return out


# --------------------------------------------------------------------- cells
def build_cells(zones, concessions, subs, protected) -> list[dict[str, Any]]:
    land = unary_union(list(zones.values()))
    minx, miny, maxx, maxy = land.bounds
    x0, y0 = (minx // CELL_M) * CELL_M, (miny // CELL_M) * CELL_M

    conc_tree = STRtree([c["geom"] for c in concessions])
    hard = [p for p in protected if p["type"] in HARD_PROTECTION]
    water = [p for p in protected if p["type"].lower().startswith("vattenskydd")]
    hard_tree = STRtree([p["geom"] for p in hard]) if hard else None
    water_tree = STRtree([p["geom"] for p in water]) if water else None

    classes = {"40": [s for s in subs if s["kv"] >= 40], "70": [s for s in subs if s["kv"] >= 70],
               "130": [s for s in subs if s["kv"] >= 130], "220": [s for s in subs if s["kv"] >= 220]}
    trees = {k: STRtree([s["pt"] for s in v]) for k, v in classes.items()}

    cells = []
    y = y0
    while y < maxy:
        x = x0
        while x < maxx:
            cell = box(x, y, x + CELL_M, y + CELL_M)
            if not cell.intersects(land):
                x += CELL_M
                continue
            land_part = cell.intersection(land)
            land_share = land_part.area / cell.area
            if land_share < 0.15:
                x += CELL_M
                continue

            zone, zbest = None, 0.0
            for z, g in zones.items():
                a = land_part.intersection(g).area if land_part.intersects(g) else 0.0
                if a > zbest:
                    zone, zbest = z, a

            owner, kv, cid, obest, owners = None, None, None, 0.0, set()
            for idx in conc_tree.query(land_part):
                c = concessions[idx]
                a = land_part.intersection(c["geom"]).area
                if a <= 0:
                    continue
                owners.add(c["owner"])
                if a > obest:
                    owner, kv, cid, obest = c["owner"], c["kv"], c["id"], a
            owner_share = obest / land_part.area if land_part.area else 0.0

            def _share(tree, items):
                if tree is None:
                    return 0.0, None
                hits = [items[i] for i in tree.query(land_part)]
                parts = [land_part.intersection(h["geom"]) for h in hits if land_part.intersects(h["geom"])]
                parts = [p for p in parts if not p.is_empty]
                if not parts:
                    return 0.0, None
                share = unary_union(parts).area / land_part.area
                biggest = max(zip(parts, hits), key=lambda ph: ph[0].area)[1]
                return share, f'{biggest["type"]}: {biggest["name"]}'

            prot_share, prot_name = _share(hard_tree, hard)
            water_share, _ = _share(water_tree, water)

            dist = {}
            nearest = {}
            for k, tree in trees.items():
                if not classes[k]:
                    dist[k] = None
                    continue
                idx, d = tree.query_nearest(land_part, return_distance=True)
                i = int(idx[0])
                dist[k] = round(float(d[0]) / 1000, 1)
                s = classes[k][i]
                nearest[k] = f'{s["name"] or "namnlös"} ({s["kv"]} kV)'
            n130_15 = len(trees["130"].query(land_part.buffer(15_000))) if classes["130"] else 0

            c4326 = transform(TO_4326, cell)
            cx, cy = TO_4326(x + CELL_M / 2, y + CELL_M / 2)
            flex = []
            for f in FLEX_MARKETS:
                fx_, fy_ = TO_3006(f["lon"], f["lat"])
                if Point(fx_, fy_).distance(land_part) <= f["radius_km"] * 1000:
                    flex.append(f["id"])

            cells.append({
                "id": f"{int(x / 1000)}_{int(y / 1000)}",
                "c": [round(cy, 4), round(cx, 4)],
                "poly": [[round(pt[1], 4), round(pt[0], 4)] for pt in list(c4326.exterior.coords)[:4]],
                "zone": zone,
                "land": round(land_share, 2),
                "dso": owner,
                "dso_share": round(owner_share, 2),
                "dso_n": len(owners),
                "conc": cid,
                "conc_kv": kv,
                "d40": dist["40"], "d70": dist["70"], "d130": dist["130"], "d220": dist["220"],
                "n40": nearest.get("40"), "n130": nearest.get("130"), "n220": nearest.get("220"),
                "k130_15": n130_15,
                "prot": round(prot_share, 2),
                "prot_name": prot_name,
                "water": round(water_share, 2),
                "flex": flex,
            })
            x += CELL_M
        y += CELL_M
    return cells


def refresh_protection() -> dict[str, int]:
    """Recompute only `prot`, `prot_name` and `water` in the published cells.

    For when Naturvårdsregistret has changed (or was read incompletely) and the
    slow OpenStreetMap download is not needed. Uses the same land clipping and
    the same shares as build_cells, so the result is identical to a full build.
    """
    zones = load_zones()
    land = unary_union(list(zones.values()))
    protected = load_protected()
    hard = [p for p in protected if p["type"] in HARD_PROTECTION]
    water = [p for p in protected if p["type"].lower().startswith("vattenskydd")]
    trees = {"hard": STRtree([p["geom"] for p in hard]), "water": STRtree([p["geom"] for p in water])}
    items = {"hard": hard, "water": water}

    def _share(kind: str, land_part) -> tuple[float, str | None]:
        hits = [items[kind][i] for i in trees[kind].query(land_part)]
        parts = [(land_part.intersection(h["geom"]), h) for h in hits if land_part.intersects(h["geom"])]
        parts = [(g, h) for g, h in parts if not g.is_empty]
        if not parts:
            return 0.0, None
        share = unary_union([g for g, _ in parts]).area / land_part.area
        biggest = max(parts, key=lambda gh: gh[0].area)[1]
        return share, f'{biggest["type"]}: {biggest["name"]}'

    path = OUT_DIR / "cells.json"
    cells = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for c in cells:
        x, y = (int(v) * 1000 for v in c["id"].split("_"))
        land_part = box(x, y, x + CELL_M, y + CELL_M).intersection(land)
        if land_part.is_empty:
            continue
        prot, name = _share("hard", land_part)
        wat, _ = _share("water", land_part)
        new = (round(prot, 2), name, round(wat, 2))
        if new != (c.get("prot"), c.get("prot_name"), c.get("water")):
            changed += 1
        c["prot"], c["prot_name"], c["water"] = new
    _write("cells.json", cells)

    meta_path = OUT_DIR / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    layer = meta["layers"]["protected"]
    layer.update({
        "retrieved_at": _retrieved("nvr_000000.gml"), "valid_from": _retrieved("nvr_000000.gml")[:10],
        "count": len(protected), "count_excluding": len(hard), "count_water": len(water),
    })
    meta["sizes_bytes"]["cells.json"] = path.stat().st_size
    _write("meta.json", meta)
    return {"protected": len(protected), "hard": len(hard), "water": len(water), "cells_changed": changed}


# ---------------------------------------------------------------------- main
def _write(name: str, payload: Any) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    zones = load_zones()
    log.info("zones ok")
    concessions = load_concessions()
    log.info("concessions: %s", len(concessions))
    subs, lines, osm_ts = load_osm()
    log.info("substations >=40kV: %s, lines >=130kV: %s", len(subs), len(lines))
    protected = load_protected()
    log.info("protected areas: %s", len(protected))
    cells = build_cells(zones, concessions, subs, protected)
    log.info("cells: %s", len(cells))

    sizes = {}
    sizes["zones.json"] = _write("zones.json", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"zone": z}, "geometry": _web_geom(g, 1500)} for z, g in sorted(zones.items())]})
    sizes["concessions.json"] = _write("concessions.json", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": c["id"], "owner": c["owner"], "kv": c["kv"]},
         "geometry": _web_geom(c["geom"], 400)} for c in concessions]})
    sizes["substations.json"] = _write("substations.json", [
        [s["lat"], s["lon"], s["kv"], s["name"], s["operator"]] for s in subs])
    sizes["lines.json"] = _write("lines.json", [
        {"kv": ln["kv"], "c": _round_coords([[p[1], p[0]] for p in transform(TO_4326, ln["geom"].simplify(150)).coords], 3)}
        for ln in lines])
    sizes["cells.json"] = _write("cells.json", cells)
    sizes["flex.json"] = _write("flex.json", FLEX_MARKETS)

    now = iso(datetime.now(TZ))
    meta = {
        "built_at": now,
        "cell_size_km": CELL_M / 1000,
        "crs_analysis": "EPSG:3006 (SWEREF99 TM)",
        "crs_web": "EPSG:4326",
        "layers": {
            "zones": {"source": "Electricity Maps (electricitymaps-contrib, geo/world.geojson), approximativ geometri för SE1–SE4",
                      "url": ZONES_URL, "retrieved_at": _retrieved("electricitymaps_world.geojson"),
                      "valid_from": "2011-11-01", "note": "Officiell geometri finns i Svenska kraftnäts nätområdesverktyg."},
            "concessions": {"source": "Energimarknadsinspektionen, Nätkoncession för område – lokalnät (shapefile)",
                            "url": EI_PAGE, "retrieved_at": _retrieved("ei_lokalnat.zip"), "valid_from": "2025-11-04",
                            "count": len(concessions), "note": "Gränserna är ungefärliga, med glipor och överlapp."},
            "substations": {"source": "OpenStreetMap-bidragsgivare (ODbL), power=substation med voltage ≥ 40 kV",
                            "url": "https://www.openstreetmap.org/copyright", "retrieved_at": _retrieved("osm_substations.json"),
                            "valid_from": osm_ts, "count": len(subs),
                            "note": "Kartlagd i OpenStreetMap, ofullständig. Ingen uppgift om ledig effekt."},
            "lines": {"source": "OpenStreetMap-bidragsgivare (ODbL), power=line med voltage ≥ 130 kV",
                      "url": "https://www.openstreetmap.org/copyright", "retrieved_at": _retrieved("osm_lines.json"),
                      "valid_from": osm_ts, "count": len(lines),
                      "note": "Visas som orientering. Inte en officiell nätkarta; Svenska kraftnät publicerar transmissionsnätet endast som PDF."},
            "protected": {"source": "Naturvårdsverket, Naturvårdsregistret WFS (SkyddadeOmraden)", "url": NVR_WFS,
                          "retrieved_at": _retrieved("nvr_000000.gml"), "valid_from": _retrieved("nvr_000000.gml")[:10],
                          "count": len(protected), "types_excluding": sorted(HARD_PROTECTION),
                          "note": "Vattenskyddsområden redovisas separat och exkluderar inte. Riksintressen och detaljplan ingår inte – kontrollera med kommunen."},
            "flex": {"source": FLEX_SOURCE, "retrieved_at": "2026-10-05", "valid_from": "2025-10-01",
                     "note": "Centrum + radie, inte nätområdets verkliga gräns."},
        },
        "sizes_bytes": sizes,
    }
    _write("meta.json", meta)
    log.info("written: %s", sizes)


if __name__ == "__main__":
    import sys

    if "--protection-only" in sys.argv:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        print(refresh_protection())
    else:
        main()
