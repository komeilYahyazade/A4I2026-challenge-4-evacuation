"""NHC GIS archive shapefiles (AL092022) -> NDJSON rows for BigQuery. Geometry from the shapes, not
the rounded LAT/LON attribute fields. Times converted to UTC."""
import datetime as dt, glob, json, re, sys, shapefile

src, out = sys.argv[1], sys.argv[2]
MON = {m: i for i, m in enumerate(["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
TZ = {"EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "AST": -4, "UTC": 0}

def advdate(s):  # '500 AM EDT Sun Sep 25 2022'
    m = re.match(r"(\d{1,2})(\d{2}) (AM|PM) (\w+) \w+ (\w+) (\d+) (\d{4})", s.strip())
    h, mi, ap, tz, mon, d, y = m.groups()
    h = int(h) % 12 + (12 if ap == "PM" else 0)
    local = dt.datetime(int(y), MON[mon], int(d), h, int(mi))
    return local - dt.timedelta(hours=TZ[tz])

def fldate(s):  # '2022-09-25 2:00 AM Sun EDT'
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{1,2}):(\d{2}) (AM|PM) \w+ (\w+)", s.strip())
    y, mo, d, h, mi, ap, tz = m.groups()
    h = int(h) % 12 + (12 if ap == "PM" else 0)
    return dt.datetime(int(y), int(mo), int(d), h, int(mi)) - dt.timedelta(hours=TZ[tz])

def iso(t): return t.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")

rows = []
for pgn in sorted(glob.glob(f"{src}/x_al092022_5day_*/*_5day_pgn.shp")):
    adv = re.search(r"-(\d{3}[A-Z]?)_5day", pgn).group(1)
    cone_r = shapefile.Reader(pgn)
    rec = cone_r.record(0).as_dict()
    cone = cone_r.shape(0).__geo_interface__          # GeoJSON, [lng, lat]
    pts_r = shapefile.Reader(pgn.replace("_pgn", "_pts"))
    track = []
    for shp, r in zip(pts_r.shapes(), pts_r.records()):
        r = r.as_dict()
        lng, lat = shp.points[0]
        track.append({"lat": round(lat, 3), "lng": round(lng, 3), "valid_at": iso(fldate(r["FLDATELBL"])),
                      "tau_h": int(r["TAU"]), "wind_kt": int(r["MAXWIND"]), "mslp_mb": int(r["MSLP"]) if r["MSLP"] else None,
                      "category": r["DVLBL"]})
    track_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lng"], p["lat"]]},
         "properties": {k: p[k] for k in ("valid_at", "tau_h", "wind_kt", "mslp_mb", "category")}}
        for p in track]}
    rows.append({"storm_id": "AL092022", "storm_name": "Ian", "advisory": str(int(adv[:3])) + adv[3:],
                 "issued_at": iso(advdate(rec["ADVDATE"])), "storm_type": rec["STORMTYPE"],
                 "cone_geojson": json.dumps(cone, separators=(",", ":")),
                 "track_json": json.dumps(track, separators=(",", ":")),
                 "track_geojson": json.dumps(track_fc, separators=(",", ":")),
                 "source": f"NOAA NHC GIS archive al092022_5day_{adv}.zip (public domain)"})

best = []
bt = shapefile.Reader(glob.glob(f"{src}/x_al092022_best_track/AL092022_pts.shp")[0])
for shp, r in zip(bt.shapes(), bt.records()):
    r = r.as_dict()
    lng, lat = shp.points[0]
    t = dt.datetime.strptime(str(r["DTG"]), "%Y%m%d%H")
    best.append({"storm_id": "AL092022", "valid_at": iso(t), "lat": round(lat, 3), "lng": round(lng, 3),
                 "wind_kt": int(r["INTENSITY"]), "mslp_mb": int(r["MSLP"]), "storm_type": r["STORMTYPE"],
                 "source": "NOAA NHC GIS best track AL092022 (public domain)"})

with open(f"{out}/storm_advisories.ndjson", "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")
with open(f"{out}/storm_best_track.ndjson", "w") as f:
    for r in best: f.write(json.dumps(r) + "\n")
for r in rows:
    t = json.loads(r["track_json"])
    print(r["advisory"], r["issued_at"], r["storm_type"], len(json.loads(r["cone_geojson"])["coordinates"][0]), "cone pts",
          "| track:", [(p["valid_at"][5:16], p["lat"], p["lng"], p["wind_kt"]) for p in t[:5]])
print("best track points:", len(best), best[0]["valid_at"], "->", best[-1]["valid_at"])
print([b for b in best if b["valid_at"].startswith("2022-09-28")])
