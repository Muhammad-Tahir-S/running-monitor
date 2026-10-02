#!/usr/bin/env python3
"""Turn an Apple Health export zip into the report payload.

The output depends only on the zip and config.json (rule M5 in COUZENS_RULES.md).
"""

import bisect
import json
import math
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
XML = "apple_health_export/export.xml"

ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
END_RE = re.compile(rb'endDate="([^"]+)"')
REC_TYPES = {
    "HKQuantityTypeIdentifierHeartRate": "hr",
    "HKQuantityTypeIdentifierRunningPower": "pwr",
    "HKQuantityTypeIdentifierRunningSpeed": "spd",
    "HKQuantityTypeIdentifierRestingHeartRate": "rhr",
    "HKQuantityTypeIdentifierVO2Max": "vo2",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "whr",
    "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute": "hrr",
    "HKQuantityTypeIdentifierBodyMass": "mass",
}
STREAMS = ("hr", "pwr", "spd")
ASLEEP = ("AsleepCore", "AsleepDeep", "AsleepREM", "AsleepUnspecified", "Asleep")


def load_config(path=None):
    return json.loads(Path(path or HERE / "config.json").read_text(encoding="utf-8"))


def attrs(text):
    return dict(ATTR_RE.findall(text))


def lead_float(value):
    if value is None:
        return None
    match = re.match(r"\s*([-+]?\d+(?:\.\d+)?)", str(value))
    return float(match.group(1)) if match else None


def parse_dt(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")


def day_ms(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def rnd(value, places):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(float(value), places)


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def pstdev(values):
    values = [v for v in values if v is not None]
    return statistics.pstdev(values) if len(values) >= 2 else None


def fmt_pace(pace):
    if pace is None:
        return None
    minutes = int(pace)
    seconds = int(round((pace - minutes) * 60))
    if seconds == 60:
        minutes, seconds = minutes + 1, 0
    return f"{minutes}:{seconds:02d}"


def fmt_day(dt):
    return f"{dt.day} {dt.strftime('%b %Y')}"


def hardware_of(device):
    if not device:
        return None
    match = re.search(r"hardware:(Watch\d+,\d+)", device) or re.search(r"hardware:([^,>&]+)", device)
    return match.group(1) if match else None


def month_key(dt):
    return f"{dt.year:04d}-{dt.month:02d}"


def activity_bucket(name):
    return {
        "Running": "run",
        "Cycling": "cycle",
        "Walking": "walk",
        "HighIntensityIntervalTraining": "hiit",
        "FunctionalStrengthTraining": "strength",
        "TraditionalStrengthTraining": "strength",
        "CoreTraining": "strength",
    }.get(name, "other")


def hr_band(hr, edges):
    if hr is None:
        return None
    labels = [f"<{edges[0]}"] + [f"{a}-{b}" for a, b in zip(edges, edges[1:])] + [f"{edges[-1]}+"]
    return labels[bisect.bisect_right(edges, hr)]


def latest_end(zpath):
    """Newest endDate in the export, as a UTC ISO string."""
    latest = None
    with zipfile.ZipFile(zpath) as archive, archive.open(XML) as handle:
        for raw in handle:
            if b"endDate=" not in raw:
                continue
            match = END_RE.search(raw)
            if match:
                stamp = match.group(1).decode()
                if latest is None or stamp[:19] > latest[:19]:
                    latest = stamp
    if latest is None:
        return None
    return parse_dt(latest).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), latest


class Plausible:
    def __init__(self, filters):
        self.limits = {
            "hr": (filters["hr_min"], filters["hr_max"]),
            "pwr": (filters["pwr_min"], filters["pwr_max"]),
            "spd": (2, filters["max_kmh"]),
        }

    def __call__(self, key, value):
        lo, hi = self.limits.get(key, (-math.inf, math.inf))
        return lo <= value <= hi


def pick_stat(rows, field):
    """Rule M2: the row spanning the workout, else sum or duration-weighted mean of laps."""
    if not rows:
        return None
    if len(rows) == 1:
        return lead_float(rows[0].get(field))
    spans = [(parse_dt(r["startDate"]), parse_dt(r["endDate"]), r) for r in rows if r.get("startDate") and r.get("endDate")]
    if spans:
        first = min(s[0] for s in spans)
        last = max(s[1] for s in spans)
        for start, end, row in spans:
            if start == first and end == last:
                return lead_float(row.get(field))
    values = [(lead_float(r.get(field)), (parse_dt(r["endDate"]) - parse_dt(r["startDate"])).total_seconds()) for r in rows if r.get("startDate")]
    values = [(v, w) for v, w in values if v is not None]
    if not values:
        return None
    if field == "sum":
        return sum(v for v, _ in values)
    weight = sum(w for _, w in values)
    return sum(v * w for v, w in values) / weight if weight else mean([v for v, _ in values])


def night_key(start):
    """Rule M3: the morning a sleep segment belongs to, or None for a nap."""
    if 11 <= start.hour < 18:
        return None
    return (start + timedelta(hours=12)).date().isoformat()


def parse_export(zpath):
    workouts = []
    streams = {key: [] for key in STREAMS}
    daily = defaultdict(list)
    nights = defaultdict(lambda: defaultdict(float))
    naps = defaultdict(float)
    in_wo = False
    wo_lines = []

    def handle_workout(text):
        match = re.search(r"<Workout\s+([^>]+)>", text)
        if not match:
            return
        meta = attrs(match.group(1))
        grouped = defaultdict(list)
        for sm in re.finditer(r"<WorkoutStatistics\s+([^>]+)/>", text):
            sa = attrs(sm.group(1))
            grouped[sa.get("type", "").replace("HKQuantityTypeIdentifier", "")].append(sa)
        metas = dict(re.findall(r'<MetadataEntry\s+key="([^"]+)"\s+value="([^"]*)"', text))
        act = meta.get("workoutActivityType", "").replace("HKWorkoutActivityType", "")
        start = parse_dt(meta["startDate"])
        end = parse_dt(meta["endDate"])
        dist = pick_stat(grouped.get("DistanceWalkingRunning"), "sum")
        if dist is None:
            dist = pick_stat(grouped.get("DistanceCycling"), "sum")
        src = meta.get("sourceName", "").replace("\xa0", " ")
        workouts.append({
            "act": act,
            "bucket": activity_bucket(act),
            "src": src,
            "watch": "Watch" in src,
            "start": start,
            "end": end,
            "t0": start.timestamp(),
            "t1": end.timestamp(),
            "dur": lead_float(meta.get("duration")) or 0,
            "dist": dist,
            "kcal": pick_stat(grouped.get("ActiveEnergyBurned"), "sum"),
            "hr": pick_stat(grouped.get("HeartRate"), "average"),
            "pwr": pick_stat(grouped.get("RunningPower"), "average"),
            "spd": pick_stat(grouped.get("RunningSpeed"), "average"),
            "gct": pick_stat(grouped.get("RunningGroundContactTime"), "average"),
            "vo": pick_stat(grouped.get("RunningVerticalOscillation"), "average"),
            "stride": pick_stat(grouped.get("RunningStrideLength"), "average"),
            "indoor": metas.get("HKIndoorWorkout") == "1",
            "temp_raw": metas.get("HKWeatherTemperature"),
            "hum_raw": metas.get("HKWeatherHumidity"),
            "elev_raw": metas.get("HKElevationAscended"),
            "hardware": hardware_of(meta.get("device", "")),
        })

    with zipfile.ZipFile(zpath) as archive, archive.open(XML) as handle:
        for raw in handle:
            line = raw.decode("utf-8", "replace")
            if in_wo:
                wo_lines.append(line)
                if "</Workout>" in line:
                    handle_workout("".join(wo_lines))
                    wo_lines, in_wo = [], False
                continue
            if "<Workout " in line:
                if "</Workout>" in line or line.rstrip().endswith("/>"):
                    handle_workout(line)
                else:
                    in_wo, wo_lines = True, [line]
                continue
            if "<Record " not in line:
                continue
            if "HKCategoryTypeIdentifierSleepAnalysis" in line:
                meta = attrs(line)
                val = meta.get("value", "").split("SleepAnalysis")[-1]
                if not (meta.get("startDate") and meta.get("endDate") and val):
                    continue
                a = parse_dt(meta["startDate"])
                b = parse_dt(meta["endDate"])
                hours = (b - a).total_seconds() / 3600
                key = night_key(a)
                if key is None:
                    if val in ASLEEP:
                        naps[a.date().isoformat()] += hours
                else:
                    nights[key][val] += hours
                continue
            type_match = re.search(r'type="([^"]+)"', line)
            if not type_match or type_match.group(1) not in REC_TYPES:
                continue
            value_match = re.search(r'value="([^"]*)"', line)
            date_match = re.search(r'startDate="([^"]+)"', line)
            value = lead_float(value_match.group(1)) if value_match else None
            if value is None or not date_match:
                continue
            kind = REC_TYPES[type_match.group(1)]
            if kind == "mass":
                unit = (re.search(r'unit="([^"]+)"', line) or [None, "kg"])[1]
                if unit == "lb":
                    value *= 0.45359237
                elif unit != "kg":
                    continue
            dt = parse_dt(date_match.group(1))
            if kind in STREAMS:
                streams[kind].append((dt.timestamp(), value))
            else:
                daily[kind].append((dt, value))

    for key in STREAMS:
        streams[key].sort()
    return workouts, streams, daily, nights, naps


def dedupe(workouts):
    """Rule M1: drop exact duplicates and non-Watch copies of Watch sessions."""
    dropped = Counter()
    seen = set()
    unique = []
    for w in sorted(workouts, key=lambda w: (w["t0"], w["t1"], w["src"], w["act"])):
        key = (w["act"], w["t0"], w["t1"], w["src"])
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        unique.append(w)
    watch = [w for w in unique if w["watch"]]
    starts = [w["t0"] for w in watch]
    kept = []
    for w in unique:
        if not w["watch"]:
            hi = bisect.bisect_left(starts, w["t1"])
            copy = False
            for x in watch[max(0, hi - 20):hi]:
                overlap = min(x["t1"], w["t1"]) - max(x["t0"], w["t0"])
                shorter = min(x["t1"] - x["t0"], w["t1"] - w["t0"])
                if shorter > 0 and overlap >= 0.5 * shorter:
                    copy = True
                    break
            if copy:
                dropped["overlap:" + w["src"]] += 1
                continue
        kept.append(w)
    return kept, dict(sorted(dropped.items()))


def slice_series(series, t0, t1):
    lo = bisect.bisect_left(series, (t0, -math.inf))
    hi = bisect.bisect_right(series, (t1, math.inf))
    return series[lo:hi]


def binned(samples, t0, t1, bin_s, plausible):
    if t1 <= t0:
        return []
    count = max(1, min(int(math.ceil((t1 - t0) / bin_s)), 2000))
    width = (t1 - t0) / count
    bins = [{"t": t0 + (i + 0.5) * width, "w": width, "hr": [], "pwr": [], "spd": []} for i in range(count)]
    for key in STREAMS:
        for t, value in samples[key]:
            if t0 <= t <= t1 and plausible(key, value):
                bins[min(count - 1, max(0, int((t - t0) / width)))][key].append(value)
    rows = []
    for item in bins:
        row = {"t": item["t"], "w": item["w"], "hr": mean(item["hr"]), "pwr": mean(item["pwr"]), "spd": mean(item["spd"])}
        if row["hr"] is not None or row["pwr"] is not None or row["spd"] is not None:
            rows.append(row)
    return rows


def near_moving(speed_times, t, window=25):
    if not speed_times:
        return True
    index = bisect.bisect_left(speed_times, t)
    return any(0 <= j < len(speed_times) and abs(speed_times[j] - t) <= window for j in (index - 1, index))


def raw_decoupling(samples, t0, t1, f, plausible):
    """Rules C1–C3: half split on moving samples for power, speed, and heart rate."""
    move = f["move_kmh"]
    need = f["min_half_samples"]
    speed_times = [t for t, v in samples["spd"] if move <= v and plausible("spd", v)]
    empty = {"n": 0, "hr": None, "pwr": None, "spd": None}
    out = {"ok": False, "dec_p": None, "dec_s": None, "a": dict(empty), "b": dict(empty)}
    if not samples["pwr"] or not samples["hr"]:
        return out
    mid = (t0 + t1) / 2

    def collect(key, lo, hi):
        vals = []
        for t, v in samples[key]:
            if t < lo or t >= hi or not plausible(key, v):
                continue
            if key == "spd":
                if v < move:
                    continue
            elif not near_moving(speed_times, t):
                continue
            vals.append(v)
        return vals

    for name, lo, hi in (("a", t0, mid), ("b", mid, t1 + 0.001)):
        pw, hr, sp = collect("pwr", lo, hi), collect("hr", lo, hi), collect("spd", lo, hi)
        out[name] = {
            "n": min(len(pw), len(hr)),
            "hr": mean(hr) if len(hr) >= need else None,
            "pwr": mean(pw) if len(pw) >= need else None,
            "spd": mean(sp) if len(sp) >= need else None,
        }
    a, b = out["a"], out["b"]

    def matched(left, right):
        return bool(left and right) and abs(left - right) / max(left, right) <= f["half_match"]

    if a["pwr"] and a["hr"] and b["pwr"] and b["hr"] and matched(a["pwr"], b["pwr"]):
        ef1, ef2 = a["pwr"] / a["hr"], b["pwr"] / b["hr"]
        out["dec_p"] = (ef1 - ef2) / ef1 * 100
    if a["spd"] and a["hr"] and b["spd"] and b["hr"] and matched(a["spd"], b["spd"]):
        ef1, ef2 = a["spd"] / a["hr"], b["spd"] / b["hr"]
        out["dec_s"] = (ef1 - ef2) / ef1 * 100
    out["ok"] = out["dec_p"] is not None or out["dec_s"] is not None
    return out


def downsample(rows, limit=110):
    if len(rows) <= limit:
        return rows
    step = len(rows) / limit
    return [rows[min(len(rows) - 1, int(i * step))] for i in range(limit)]


def pace_hist(rows, move):
    """Moving minutes per whole second of pace (sec/km), from the 15-second bins."""
    hist = defaultdict(float)
    for r in rows:
        if r["spd"] is not None and r["spd"] >= move:
            hist[int(3600 / r["spd"])] += r["w"] / 60
    return [[sec, round(minutes, 3)] for sec, minutes in sorted(hist.items())]


def weather(workout):
    temp = lead_float(workout["temp_raw"])
    if temp is not None and "F" in (workout["temp_raw"] or ""):
        temp = (temp - 32) * 5 / 9
    hum = lead_float(workout["hum_raw"])
    if hum is not None and hum > 100:
        hum = hum / 100
    elev = lead_float(workout["elev_raw"])
    if elev is not None and "cm" in (workout["elev_raw"] or ""):
        elev = elev / 100
    return temp, hum, elev


def build_payload(zpath, cfg=None):
    cfg = cfg or load_config()
    f = cfg["filters"]
    z = cfg["zones"]
    plausible = Plausible(f)
    move = f["move_kmh"]
    bin_s = f["bin_s"]
    split = cfg["eras"]["split"]

    end = latest_end(zpath)
    raw_workouts, streams, daily, nights, naps = parse_export(zpath)
    workouts, dropped = dedupe(raw_workouts)
    as_of = parse_dt(end[1]) if end else max(w["end"] for w in workouts)

    runs = []
    months = defaultdict(lambda: {k: 0.0 for k in (
        "run_h", "cycle_h", "walk_h", "hiit_h", "strength_h", "other_h", "run_km", "ped_kcal", "other_kcal"
    )})
    hardware_runs = Counter()
    all_runs = []
    days = defaultdict(lambda: {"easy": 0.0, "hard": 0.0, "run": 0.0})

    for workout in workouts:
        mk = month_key(workout["start"])
        bucket = workout["bucket"]
        months[mk][f"{bucket}_h"] += workout["dur"] / 60
        kcal = workout["kcal"] or 0
        months[mk]["ped_kcal" if bucket in ("run", "walk") else "other_kcal"] += kcal
        day = workout["start"].date().isoformat()
        if workout["hr"]:
            days[day]["hard" if workout["hr"] >= z["hard_bpm"] else "easy"] += workout["dur"]
        if bucket == "run":
            days[day]["run"] += workout["dur"]
            if workout["dist"]:
                months[mk]["run_km"] += workout["dist"]
            all_runs.append({
                "date": day,
                "src": "watch" if workout["watch"] else workout["src"],
                "dur": rnd(workout["dur"], 2),
                "dist": rnd(workout["dist"], 3),
                "hr": rnd(workout["hr"], 1),
                "indoor": workout["indoor"],
            })

        if not (workout["act"] == "Running" and workout["watch"] and workout["pwr"] and workout["hr"] and workout["spd"]):
            continue
        hardware_runs[workout["hardware"]] += 1
        samples = {key: slice_series(streams[key], workout["t0"], workout["t1"]) for key in STREAMS}
        rows = binned(samples, workout["t0"], workout["t1"], bin_s, plausible)
        dec = raw_decoupling(samples, workout["t0"], workout["t1"], f, plausible)
        speeds = [r["spd"] for r in rows if r["spd"] is not None and r["spd"] >= move]
        speed_sd, speed_mu = pstdev(speeds), mean(speeds)
        cv = speed_sd / speed_mu if speed_sd is not None and speed_mu else None
        steady_pace = cv is not None and cv <= f["steady_cv"]
        steady = bool(workout["dur"] >= f["min_steady_min"] and steady_pace and dec["ok"])
        temp, hum, elev = weather(workout)
        pace = 60 / workout["spd"] if workout["spd"] else None
        ef = workout["pwr"] / workout["hr"] if workout["pwr"] and workout["hr"] else None
        trace_rows = downsample(rows)
        runs.append({
            "id": len(runs),
            "t": day_ms(workout["start"].date()),
            "label": f"{fmt_day(workout['start'])} · {workout['dur']:.0f} min",
            "date": day,
            "start": workout["start"].strftime("%H:%M"),
            "dur": rnd(workout["dur"], 1),
            "hr": rnd(workout["hr"], 1),
            "pwr": rnd(workout["pwr"], 1),
            "pace": rnd(pace, 2),
            "spd": rnd(workout["spd"], 2),
            "dist": rnd(workout["dist"], 2),
            "ef": rnd(ef, 3),
            "band": hr_band(workout["hr"], z["hr_bands"]),
            "decP": rnd(dec["dec_p"], 2),
            "decS": rnd(dec["dec_s"], 2),
            "cv": rnd(cv, 3),
            "steadyPace": steady_pace,
            "steady": steady,
            "indoor": workout["indoor"],
            "temp": rnd(temp, 1),
            "hum": rnd(hum, 0),
            "elev": rnd(elev, 1),
            "gct": rnd(workout["gct"], 0),
            "vo": rnd(workout["vo"], 2),
            "stride": rnd(workout["stride"], 2),
            "kcal": rnd(workout["kcal"], 0),
            "hr1": rnd(dec["a"]["hr"], 1),
            "hr2": rnd(dec["b"]["hr"], 1),
            "pwr1": rnd(dec["a"]["pwr"], 1),
            "pwr2": rnd(dec["b"]["pwr"], 1),
            "spd1": rnd(dec["a"]["spd"], 2),
            "spd2": rnd(dec["b"]["spd"], 2),
            "n1": dec["a"]["n"],
            "n2": dec["b"]["n"],
            "paceHist": pace_hist(rows, move),
            "trace": {
                "min": [rnd((r["t"] - workout["t0"]) / 60, 2) for r in trace_rows],
                "hr": [rnd(r["hr"], 1) for r in trace_rows],
                "pwr": [rnd(r["pwr"], 1) for r in trace_rows],
                "pace": [rnd(60 / r["spd"], 2) if r["spd"] and r["spd"] > 1 else None for r in trace_rows],
            },
        })

    watch_runs = [w for w in workouts if w["act"] == "Running" and w["watch"]]
    binned_runs = []
    for workout in watch_runs:
        samples = {key: slice_series(streams[key], workout["t0"], workout["t1"]) for key in STREAMS}
        rows = [r for r in binned(samples, workout["t0"], workout["t1"], bin_s, plausible) if r["spd"] and r["spd"] >= move and r["pwr"]]
        binned_runs.append((workout["t1"] - workout["t0"], rows))

    def mmp_for(duration_min):
        best_pwr = best_spd = None
        window = duration_min * 60
        min_bins = max(3, int(window / bin_s * 0.7))
        for span, rows in binned_runs:
            if span < window * 0.9 or len(rows) < min_bins:
                continue
            j = 0
            for i, row in enumerate(rows):
                while rows[j]["t"] < row["t"] - window:
                    j += 1
                if row["t"] - rows[j]["t"] + bin_s < window * 0.8:
                    continue
                chunk = rows[j:i + 1]
                if len(chunk) < min_bins:
                    continue
                pw = mean([r["pwr"] for r in chunk])
                sp = mean([r["spd"] for r in chunk])
                if pw is not None and (best_pwr is None or pw > best_pwr):
                    best_pwr = pw
                if sp is not None and (best_spd is None or sp > best_spd):
                    best_spd = sp
        return best_pwr, best_spd

    mmp = []
    for minutes in (1, 2, 5, 10, 20, 30, 45, 60):
        pwr, spd = mmp_for(minutes)
        mmp.append({"min": minutes, "pwr": rnd(pwr, 1), "spd": rnd(spd, 2), "pace": rnd(60 / spd, 2) if spd else None})

    def daily_series(key, places):
        grouped = defaultdict(list)
        for dt, value in daily[key]:
            grouped[dt.date()].append(value)
        return [{"t": day_ms(d), "d": d.isoformat(), "v": rnd(median(grouped[d]), places)} for d in sorted(grouped)]

    floor = cfg["months_from"]
    present = sorted(k for k in months if k >= floor)
    if present:
        y, m = map(int, present[0].split("-"))
        y2, m2 = map(int, present[-1].split("-"))
        while (y, m) <= (y2, m2):
            months[f"{y:04d}-{m:02d}"]
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    month_rows = []
    for key in sorted(months):
        if key >= floor:
            month_rows.append({"m": key, **{k: rnd(v, 2) for k, v in months[key].items()}})

    long_runs = [r for r in runs if r["dur"] >= f["min_steady_min"]]
    early = [r for r in long_runs if r["date"] < split]
    late = [r for r in long_runs if r["date"] >= split]
    lo, hi = z["easy_hr"]
    band_label = f"{lo}-{hi}"
    band_early = [r for r in early if r["band"] == band_label]
    band_late = [r for r in late if r["band"] == band_label]
    steady40 = [r for r in runs if r["steady"] and r["dur"] >= cfg["couzens"]["long_run_min"] and r["decP"] is not None]

    def med(rows, key, places):
        return rnd(median([r[key] for r in rows]), places)

    finding = {
        "nInstrumented": len(runs),
        "n20": len(long_runs),
        "nEarly": len(early),
        "nLate": len(late),
        "earlyHr": med(early, "hr", 0),
        "lateHr": med(late, "hr", 0),
        "earlyPwr": med(early, "pwr", 0),
        "latePwr": med(late, "pwr", 0),
        "earlyPace": fmt_pace(median([r["pace"] for r in early])),
        "latePace": fmt_pace(median([r["pace"] for r in late])),
        "earlyEf": med(early, "ef", 2),
        "lateEf": med(late, "ef", 2),
        "bandEarlyN": len(band_early),
        "bandLateN": len(band_late),
        "bandEarlyEf": med(band_early, "ef", 2),
        "bandLateEf": med(band_late, "ef", 2),
        "steady40N": len(steady40),
        "steady40Dec": rnd(median([r["decP"] for r in steady40]), 1),
        "dateMin": min((r["date"] for r in runs), default=None),
        "dateMax": max((r["date"] for r in runs), default=None),
    }

    mass = sorted(daily["mass"], key=lambda item: item[0])
    payload = {
        "asOf": as_of.strftime("%Y-%m-%d %H:%M"),
        "asOfDate": as_of.date().isoformat(),
        "config": {k: cfg[k] for k in ("eras", "filters", "zones", "couzens", "plan", "pace_bands", "records")},
        "hardware": hardware_runs.most_common(),
        "dropped": dropped,
        "finding": finding,
        "runs": runs,
        "allRuns": all_runs,
        "days": [{"d": d, "easy": rnd(v["easy"], 1), "hard": rnd(v["hard"], 1), "run": rnd(v["run"], 1)} for d, v in sorted(days.items())],
        "months": month_rows,
        "mmp": mmp,
        "rhr": daily_series("rhr", 1),
        "vo2": daily_series("vo2", 2),
        "hrv": daily_series("hrv", 1),
        "whr": daily_series("whr", 1),
        "hrr": daily_series("hrr", 1),
        "mass": {"kg": rnd(mass[-1][1], 1), "date": mass[-1][0].date().isoformat()} if mass else None,
    }
    add_recovery(payload, cfg, nights, naps)
    return payload


def add_recovery(payload, cfg, nights, naps):
    z = cfg["zones"]
    rows = []
    for key in sorted(nights):
        c = nights[key]
        core, deep, rem = c.get("AsleepCore", 0.0), c.get("AsleepDeep", 0.0), c.get("AsleepREM", 0.0)
        total = core + deep + rem + c.get("AsleepUnspecified", 0.0) + c.get("Asleep", 0.0)
        if total <= 0:
            continue
        d = date.fromisoformat(key)
        rows.append({
            "t": day_ms(d), "date": key, "asleep": rnd(total, 2), "core": rnd(core, 2),
            "deep": rnd(deep, 2), "rem": rnd(rem, 2), "awake": rnd(c.get("Awake", 0.0), 2),
            "nap": rnd(naps.get(key, 0.0), 2),
        })
    by_date = {r["date"]: r for r in rows}

    by_day = {d["d"]: d for d in payload["days"]}
    load = []
    if rows:
        start = date.fromisoformat(cfg["load_from"])
        last = date.fromisoformat(payload["asOfDate"])
        chronic_from = start + timedelta(days=21)
        day = start
        while day <= last:
            e7 = h7 = e28 = h28 = 0.0
            for i in range(28):
                item = by_day.get((day - timedelta(days=i)).isoformat())
                if not item:
                    continue
                e28 += item["easy"]
                h28 += item["hard"]
                if i < 7:
                    e7 += item["easy"]
                    h7 += item["hard"]
            w = z["hard_weight"]
            load.append({
                "t": day_ms(day),
                "easy7": rnd(e7 / 60, 2),
                "hard7": rnd(h7 / 60, 2),
                "acute": rnd((e7 + w * h7) / 60, 2),
                "chronic": rnd((e28 + w * h28) / 60 / 4, 2) if day >= chronic_from else None,
            })
            day += timedelta(days=1)

    joins = []
    for r in payload["runs"]:
        night = by_date.get(r["date"])
        if night and (r["dur"] or 0) >= cfg["filters"]["min_steady_min"]:
            joins.append({"id": r["id"], "sleep": night["asleep"], "deep": night["deep"], "rem": night["rem"]})

    payload["recovery"] = {"nights": rows, "load": load, "joins": joins, "hardBpm": z["hard_bpm"]}


if __name__ == "__main__":
    print("Run sync_server.py, or: python3 sync_server.py --build")
