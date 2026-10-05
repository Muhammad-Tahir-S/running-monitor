#!/usr/bin/env python3
"""Couzens rules as code. Each check carries a rule ID from COUZENS_RULES.md.

`evaluate` reads only the payload, the config, and the journal, so the same inputs
always give the same checks (rule M5).
"""

import math
import statistics
from datetime import date, timedelta

DEC = "http://alancouzens.blogspot.com/2009/10/science-of-decoupling.html"
SPEED = "http://alancouzens.blogspot.com/2008/09/real-world-periodization-iv-need-for.html"
RULES = {
    "C1": ("Decoupling is the change in output per beat from the first half to the second half", DEC),
    "C2": ("About ±5% is fatigue at a safe level. A larger swing either way is a warning", DEC),
    "C3": ("Score decoupling on steady sessions only, not intervals", DEC),
    "C4": ("Compare efficiency factor only between similar efforts", "https://alancouzens.com/blog/kona_conditions.html"),
    "C5": ("Heat lowers efficiency factor. A hot easy run is not lost fitness", "https://alancouzens.com/blog/kona_conditions.html"),
    "C6": ("Most work is easy, at or under the aerobic threshold", "https://alancouzens.com/blog/endurance_physiology_101.html"),
    "C7": ("Volume and intensity are two loads. Track them apart", "https://www.alancouzens.com/blog/vol_int_responder.html"),
    "C8": ("Monthly volume is the base dose for aerobic fitness", "https://www.alancouzens.com/blog/volume-vs-intensity.php"),
    "C9": ("A base week has a bulk of easy work and one session about 1.5× the average", "http://alancouzens.blogspot.com/2009/10/destructing-your-annual-training-plan.html"),
    "C10": ("Keep work at or above threshold near 5% of the week in base", "http://alancouzens.blogspot.com/2013/08/building-your-performance-period-iii.html"),
    "C11": ("Speed work is 3K to 10K pace, from the pace you can hold now", SPEED),
    "C12": ("Two speed sessions a week are enough under VO2max 40", SPEED),
    "C13": ("In a speed phase keep 66–80% of the recent peak volume", SPEED),
    "C14": ("Sharpening has most effect 2–10 weeks before the event", "https://www.alancouzens.com/blog/periodization.html"),
    "C15": ("Heart rate 5% off normal at a known pace means recovery is incomplete", DEC),
    "C16": ("HRV quartile sets the day: top 25% intensity, middle aerobic, bottom 25% recovery, bottom 3% rest", "https://alancouzens.com/blog/overtraining_HRV.html"),
    "C17": ("Readiness combines HRV, resting HR, sleep, mood, soreness, fatigue, and stress", "https://alancouzens.com/TP/athletes.cgi/blog/readiness"),
    "C18": ("Adaptation is fatigue, then recovery, then a higher level", DEC),
    "C19": ("Best power against duration gives a fatigue index. When duration doubles, power falls by 1 − 2 to that power", "https://alancouzens.com/endurancecorner/2015/09/fatigue-curves/"),
    "C20": ("Tiredness clears in four phases: inside the session, over 1–4 days, over 1–2 weeks, and over months", "http://alancouzens.blogspot.com/2009/10/fatigue-curve.html"),
    "C21": ("After the key races, cut weekly run time to 10–40% for about 4 weeks, then hold about half, with specific work waiting until about 60 days have passed", "https://alancouzens.com/blog/off_season.html"),
    "P1": ("Plan week: two speed runs, two easy runs, one long run", None),
    "P2": ("Plan 5K pace means the 5K you can run now", None),
    "P3": ("Goal pace and faster stay a short set", None),
}
LEVELS = ["rest", "recovery", "aerobic", "intensity"]
LEVEL_TEXT = {
    "intensity": "Ready for a key session",
    "aerobic": "Aerobic day",
    "recovery": "Recovery day",
    "rest": "Rest day",
}
SCORES = {"pass": 1.0, "partial": 0.5, "fail": 0.0, "na": 0.0}


def continuous(run):
    """Rule C3: a run-walk interval is not a steady aerobic session."""
    return not run.get("runWalk")


def secs(text):
    parts = [int(p) for p in str(text).strip().split(":")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def mmss(sec):
    if sec is None:
        return "—"
    sec = int(math.floor(sec + 0.5))
    return f"{sec // 60}:{sec % 60:02d}"


def hms(sec):
    if sec is None:
        return "—"
    sec = int(math.floor(sec + 0.5))
    h, rest = divmod(sec, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def med(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def r(value, places=1):
    return None if value is None else round(value, places)


def monday(day):
    return day - timedelta(days=day.weekday())


def spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 5:
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def check(rule, name, result, evidence):
    return {"rule": rule, "name": name, "result": result, "score": SCORES[result], "evidence": evidence}


def minutes_in(run, lo, hi):
    return sum(m for sec, m in run.get("paceHist") or [] if lo <= sec < hi)


def records(p, cfg, journal):
    exclude_indoor = cfg["filters"].get("exclude_indoor_records", True)

    def best_for(km, runs, races):
        best = None
        for run in runs:
            if not run["dist"] or not run["dur"] or (exclude_indoor and run["indoor"]):
                continue
            if not 0.97 * km <= run["dist"] <= 2 * km:
                continue
            pace = run["dur"] * 60 / run["dist"]
            if best is None or pace < best["paceS"]:
                hr = f", {run['hr']:.0f} bpm" if run["hr"] else ""
                best = {"paceS": pace, "date": run["date"], "source": "Workout",
                        "detail": f"{run['dist']:.2f} km in {hms(run['dur'] * 60)}{hr}"}
        for race in races:
            if abs(race["km"] - km) / km <= 0.03:
                pace = secs(race["time"]) / race["km"]
                if best is None or pace < best["paceS"]:
                    best = {"paceS": pace, "date": race["date"], "source": "Journal race",
                            "detail": race.get("name") or f"{race['km']} km race"}
        return best

    races = journal.get("races", [])
    rows = []
    for target in cfg["records"]:
        best = best_for(target["km"], p["allRuns"], races)
        if best:
            rows.append({"name": target["name"], "km": target["km"], "time": hms(best["paceS"] * target["km"]),
                         "seconds": round(best["paceS"] * target["km"]), "pace": mmss(best["paceS"]),
                         "date": best["date"], "source": best["source"], "detail": best["detail"]})
        else:
            rows.append({"name": target["name"], "km": target["km"], "time": None, "seconds": None,
                         "pace": None, "date": None, "source": None, "detail": "No run covers this distance"})
    five = best_for(5, p["allRuns"], races)
    for row in rows:
        row["riegel"] = hms(five["paceS"] * 5 * (row["km"] / 5) ** 1.06) if five else None
    split = cfg["eras"]["split"]
    before = best_for(5, [x for x in p["allRuns"] if x["date"] < split], [x for x in races if x["date"] < split])
    after = best_for(5, [x for x in p["allRuns"] if x["date"] >= split], [x for x in races if x["date"] >= split])
    return rows, five, before, after


def derived_bands(cfg, five):
    bands = [{"key": b["key"], "name": b["name"], "lo": secs(b["from"]), "hi": secs(b["to"]), "rule": "P3"} for b in cfg["pace_bands"]]
    if five:
        p5 = five["paceS"]
        lo, hi = p5 * 0.6 ** 0.06, p5 * 2 ** 0.06
        bands.insert(0, {"key": "speed", "name": f"3K–10K pace now, {mmss(lo)}–{mmss(hi)}", "lo": lo, "hi": hi, "rule": "C11"})
        bands.insert(1, {"key": "fivek", "name": f"5K pace now, {mmss(p5 * 0.97)}–{mmss(p5 * 1.03)}", "lo": p5 * 0.97, "hi": p5 * 1.03, "rule": "P2"})
    return bands


def readiness(p, cfg, journal, day):
    c = cfg["couzens"]
    band = c["readiness_band"] / 100
    notes = []
    flags = []
    hrv = [(date.fromisoformat(x["d"]), x["v"]) for x in p["hrv"] if x["v"] is not None]
    today = [v for d, v in hrv if day - timedelta(days=1) <= d <= day]
    window = [v for d, v in hrv if day - timedelta(days=c["hrv_window_days"]) <= d < day - timedelta(days=1)]
    pct = None
    if today and len(window) >= 20:
        value = today[-1]
        pct = sum(v < value for v in window) / len(window)
        base = 3 if pct >= 0.75 else 2 if pct >= 0.25 else 1 if pct >= 0.03 else 0
        notes.append({"rule": "C16", "text": f"SDNN {value:.0f} ms is higher than {pct * 100:.0f}% of the last {c['hrv_window_days']} days."})
    else:
        base = 2
        notes.append({"rule": "C16", "text": "No SDNN reading near this day. The base level is an aerobic day."})

    rhr = [(date.fromisoformat(x["d"]), x["v"]) for x in p["rhr"] if x["v"] is not None]
    now = [v for d, v in rhr if d == day]
    prior = [v for d, v in rhr if day - timedelta(days=c["baseline_days"]) <= d < day]
    if now and len(prior) >= 7:
        m = med(prior)
        if now[-1] > m * (1 + band):
            flags.append({"rule": "C17", "text": f"Resting heart rate {now[-1]:.0f} bpm is {(now[-1] / m - 1) * 100:.0f}% above the {c['baseline_days']}-day median of {m:.0f}."})

    night = next((n for n in p["recovery"]["nights"] if n["date"] == day.isoformat()), None)
    if night and night["asleep"] < c["sleep_target_h"]:
        flags.append({"rule": "C17", "text": f"Slept {night['asleep']:.1f} h. The target is {c['sleep_target_h']} h."})

    lo, hi = (secs(x) / 60 for x in c["known_pace"])
    known = [x for x in p["runs"] if continuous(x) and x["pace"] and lo <= x["pace"] <= hi and x["dur"] >= cfg["filters"]["min_steady_min"]]
    recent = [x for x in known if day - timedelta(days=7) <= date.fromisoformat(x["date"]) < day]
    if recent:
        last = recent[-1]
        d0 = date.fromisoformat(last["date"])
        base_runs = [x["hr"] for x in known if d0 - timedelta(days=c["baseline_days"]) <= date.fromisoformat(x["date"]) < d0]
        if len(base_runs) >= 3:
            m = med(base_runs)
            off = (last["hr"] / m - 1) * 100
            if abs(off) > c["readiness_band"]:
                word = "above" if off > 0 else "below"
                flags.append({"rule": "C15", "text": f"On {last['date']} heart rate at {mmss(lo * 60)}–{mmss(hi * 60)} /km was {last['hr']:.0f} bpm, {abs(off):.0f}% {word} the usual {m:.0f}."})

    entry = next((e for e in journal.get("entries", []) if e.get("date") == day.isoformat()), None)
    if entry:
        for key in ("mood", "soreness", "fatigue", "stress"):
            value = entry.get(key)
            if value is not None and value <= 2:
                flags.append({"rule": "C17", "text": f"Journal {key} is {value} of 5."})

    level = base - len(flags)
    if len(flags) >= 2:
        level = min(level, 1)
    level = max(0, min(3, level))
    return {"date": day.isoformat(), "level": LEVELS[level], "text": LEVEL_TEXT[LEVELS[level]],
            "base": LEVELS[base], "hrvPct": r(pct, 2), "flags": flags, "notes": notes,
            "journal": bool(entry)}


def weeks_table(p, cfg, bands, as_of):
    c = cfg["couzens"]
    z = cfg["zones"]
    f = cfg["filters"]
    easy_lo, easy_hi = z["easy_hr"]
    speed = next((b for b in bands if b["key"] == "speed"), None)
    start = monday(date.fromisoformat(cfg["weeks_from"]))
    last = monday(as_of)
    by_week = {}

    def key_of(text):
        return monday(date.fromisoformat(text)).isoformat()

    def bucket(name, item, text):
        by_week.setdefault(key_of(text), {}).setdefault(name, []).append(item)

    for x in p["allRuns"]:
        bucket("all", x, x["date"])
    for x in p["runs"]:
        bucket("runs", x, x["date"])
    for x in p["days"]:
        bucket("days", x, x["d"])
    for x in p["recovery"]["nights"]:
        bucket("nights", x, x["date"])
    for x in p["rhr"]:
        bucket("rhr", x, x["d"])
    for x in p["hrv"]:
        bucket("hrv", x, x["d"])

    rows = []
    week = start
    while week <= last:
        data = by_week.get(week.isoformat(), {})
        alls = data.get("all", [])
        runs = data.get("runs", [])
        steady_runs = [x for x in runs if continuous(x)]
        durs = [x["dur"] for x in alls if x["dur"]]
        longest = max(steady_runs, key=lambda x: x["dur"], default=None)
        long_dec = longest["decP"] if longest and longest["dur"] >= c["long_run_min"] else None
        easy_runs = [x for x in steady_runs if x["dur"] >= f["min_steady_min"] and easy_lo <= x["hr"] <= easy_hi]
        days = data.get("days", [])
        row = {
            "week": week.isoformat(),
            "partial": week == last and as_of.weekday() < 6,
            "runs": len(alls),
            "runH": r(sum(durs) / 60, 2),
            "easyH": r(sum(x["easy"] for x in days) / 60, 2),
            "hardH": r(sum(x["hard"] for x in days) / 60, 2),
            "longMin": r(max(durs, default=0), 0),
            "longRatio": r(max(durs) / statistics.mean(durs), 2) if len(durs) >= 2 else None,
            "longDec": r(long_dec, 1),
            "easyPace": r(med([x["pace"] for x in easy_runs]), 2),
            "easyEf": r(med([x["ef"] for x in easy_runs]), 3),
            "sleep": r(med([x["asleep"] for x in data.get("nights", [])]), 2),
            "rhr": r(med([x["v"] for x in data.get("rhr", [])]), 1),
            "hrv": r(med([x["v"] for x in data.get("hrv", [])]), 1),
            "speedDays": sum(1 for x in runs if speed and minutes_in(x, 0, speed["hi"]) >= cfg["plan"]["speed_day_min"]),
        }
        for b in bands:
            row[b["key"]] = r(sum(minutes_in(x, b["lo"], b["hi"]) for x in runs), 1)
        if speed:
            row["fast"] = r(sum(minutes_in(x, 0, speed["hi"]) for x in runs), 1)
        rows.append(row)
        week += timedelta(days=7)
    return rows


def _log_fit(pairs):
    """Log-log fit of output against duration in hours.

    Couzens reads the value at 1 hour as the FTP-style anchor, and the exponent
    as the fatigue index. The percent fall when duration doubles is 1 − 2^index.
    """
    xs, ys = [], []
    for minutes, value in pairs:
        if minutes and value and value > 0:
            xs.append(math.log(minutes / 60.0))
            ys.append(math.log(value))
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    anchor = math.exp(my - slope * mx)
    return {"n": n, "index": slope, "anchor": anchor, "fade": (1 - 2 ** slope) * 100}


def fatigue_curve(mmp):
    """Rule C19. Best efforts already in the payload. Three durations minimum."""
    power = _log_fit([(p.get("min"), p.get("pwr")) for p in mmp])
    speed = _log_fit([(p.get("min"), p.get("spd")) for p in mmp])
    if not power and not speed:
        return None
    points = []
    for p in mmp:
        row = {"min": p.get("min"), "pwr": p.get("pwr"), "spd": p.get("spd"), "pace": p.get("pace")}
        minutes = p.get("min")
        if power and minutes:
            row["fitPwr"] = r(power["anchor"] * (minutes / 60) ** power["index"], 1)
        if speed and minutes:
            fit = speed["anchor"] * (minutes / 60) ** speed["index"]
            row["fitSpd"] = r(fit, 2)
            row["fitPace"] = r(60 / fit, 2) if fit else None
        points.append(row)
    longest = max((p["min"] for p in points if p.get("min") and (p.get("pwr") or p.get("spd"))), default=None)

    def pack(fit, extra):
        if not fit:
            return None
        return {"n": fit["n"], "index": r(fit["index"], 4), "fade": r(fit["fade"], 1), **extra}

    return {
        "power": pack(power, {"hourW": r(power["anchor"], 1)}) if power else None,
        "speed": pack(speed, {"hourKmh": r(speed["anchor"], 2), "hourPace": r(60 / speed["anchor"], 2)}) if speed else None,
        "longest": longest,
        "points": points,
    }


def peak_volume(weeks, cfg):
    c = cfg["couzens"]
    plan_start = date.fromisoformat(cfg["plan"]["start"])
    lo = plan_start - timedelta(weeks=c["peak_lookback_weeks"])
    hours = [(date.fromisoformat(w["week"]), w["runH"] or 0) for w in weeks]
    best = None
    for i in range(3, len(hours)):
        d = hours[i][0]
        if lo <= d < plan_start:
            avg = statistics.mean(h for _, h in hours[i - 3:i + 1])
            if best is None or avg > best[1]:
                best = (hours[i - 3][0].isoformat(), avg)
    return best


def base_checks(p, cfg, weeks, rec_before, rec_after, as_of):
    c = cfg["couzens"]
    z = cfg["zones"]
    split = cfg["eras"]["split"]
    limit = c["decoupling_limit"]
    out = []

    current_month = as_of.strftime("%Y-%m")
    months = [m for m in p["months"] if m["m"] != current_month]
    before = [m["run_h"] for m in months if m["m"] < split[:7]][-6:]
    after = [m["run_h"] for m in months if m["m"] >= split[:7]]
    mb, ma = med(before), med(after)
    if mb is None or ma is None:
        out.append(check("C8", "More monthly volume", "na", "Not enough full months on both sides of the split."))
    else:
        change = (ma / mb - 1) * 100 if mb else 100
        res = "pass" if change >= 10 else "partial" if change > -10 else "fail"
        out.append(check("C8", "More monthly volume", res,
                         f"Median run time is {ma:.1f} h a month from {split[:7]}, against {mb:.1f} h in the 6 months before ({change:+.0f}%). Duplicate and copied workouts are removed (M1)."))

    full = [w for w in weeks if not w["partial"]][-8:]
    easy = sum(w["easyH"] or 0 for w in full)
    hard = sum(w["hardH"] or 0 for w in full)
    if easy + hard:
        share = easy / (easy + hard)
        res = "pass" if share >= c["easy_share_min"] else "partial" if share >= c["easy_share_min"] - 0.1 else "fail"
        out.append(check("C6", "Most work is easy", res,
                         f"{share * 100:.0f}% of workout time in the last {len(full)} full weeks has a mean heart rate under {z['hard_bpm']} bpm. The target is {c['easy_share_min'] * 100:.0f}% or more (C9)."))
    else:
        out.append(check("C6", "Most work is easy", "na", "No workouts with heart rate in the last 8 weeks."))

    steady = [x for x in p["runs"] if continuous(x) and x["date"] >= split and x["steady"] and x["dur"] >= c["long_run_min"] and x["decP"] is not None]
    if steady:
        m = med([x["decP"] for x in steady])
        inside = sum(abs(x["decP"]) <= limit for x in steady)
        res = "pass" if abs(m) <= limit and inside >= 0.75 * len(steady) else "partial" if abs(m) <= limit else "fail"
        out.append(check("C2", "Long steady runs are durable", res,
                         f"Median decoupling is {m:.1f}% on {len(steady)} steady runs of {c['long_run_min']} min or more. {inside} are inside ±{limit}%, {len(steady) - inside} are outside."))
    else:
        out.append(check("C2", "Long steady runs are durable", "na", "No steady long run with a decoupling value since the split."))

    lo, hi = z["easy_hr"]
    band = [x for x in p["runs"] if continuous(x) and x["date"] >= split and x["dur"] >= cfg["filters"]["min_steady_min"] and lo <= x["hr"] <= hi and x["ef"]]
    if len(band) >= 6:
        first = band[:max(3, len(band) // 4)]
        recent_from = (as_of - timedelta(weeks=8)).isoformat()
        last = [x for x in band if x["date"] >= recent_from] or band[-max(3, len(band) // 4):]
        e1, e2 = med([x["ef"] for x in first]), med([x["ef"] for x in last])
        t1, t2 = med([x["temp"] for x in first]), med([x["temp"] for x in last])
        change = (e2 / e1 - 1) * 100
        res = "pass" if change >= 3 else "partial" if change > -3 else "fail"
        heat = ""
        if t1 is not None and t2 is not None and abs(t2 - t1) >= 2:
            heat = f" The recent runs are {abs(t2 - t1):.0f}°C {'hotter' if t2 > t1 else 'cooler'} (C5), so part of the change is the air."
        out.append(check("C4", "Faster at the same easy heart rate", res,
                         f"Efficiency factor at {lo}–{hi} bpm is {e2:.2f} W/bpm in the last 8 weeks ({len(last)} runs), against {e1:.2f} in the first {len(first)} runs of the block ({change:+.1f}%).{heat}"))
    else:
        out.append(check("C4", "Faster at the same easy heart rate", "na", f"Fewer than 6 runs at {lo}–{hi} bpm since the split."))

    if rec_after and (not rec_before or rec_after["paceS"] < rec_before["paceS"]):
        out.append(check("C11", "Faster hard mark after the base", "pass",
                         f"Best 5 km after the split is {hms(rec_after['paceS'] * 5)} on {rec_after['date']}."))
    else:
        tail = f" The best 5 km is {hms(rec_before['paceS'] * 5)} on {rec_before['date']}, before the split." if rec_before else ""
        out.append(check("C11", "Faster hard mark after the base", "fail",
                         "No faster 5 km after the split." + tail))
    return out


def plan_checks(p, cfg, journal, weeks, bands, as_of, peak):
    plan = cfg["plan"]
    c = cfg["couzens"]
    start = date.fromisoformat(plan["start"])
    end = start + timedelta(weeks=plan["weeks"])
    if not start <= as_of < end + timedelta(days=7):
        return None
    week = weeks[-1]
    week_start = date.fromisoformat(week["week"])
    elapsed = min(7, (as_of - week_start).days + 1)
    runs = [x for x in p["runs"] if monday(date.fromisoformat(x["date"])) == week_start]
    band = {b["key"]: b for b in bands}
    speed = band.get("speed")
    speed_runs = [x for x in runs if speed and minutes_in(x, 0, speed["hi"]) >= plan["speed_day_min"]]
    speed_dates = {x["date"] for x in speed_runs}
    easy_runs = [x for x in runs if x["date"] not in speed_dates]
    easy_dates = sorted({x["date"] for x in easy_runs})
    number = (week_start - start).days // 7 + 1

    nike = []
    n = len(speed_runs)
    days = ", ".join(sorted({x["date"] for x in speed_runs})) or "none yet"
    nike.append(check("P1", "Two speed days", "pass" if n >= plan["speed_days"] else "partial" if n else "fail", f"{n} this week: {days}."))
    hard_easy = [x for x in easy_runs if x["hr"] >= cfg["zones"]["hard_bpm"]]
    res = "fail" if hard_easy else "pass" if len(easy_dates) >= plan["easy_days"] else "partial"
    detail = "; ".join(f"{x['date']} {mmss(x['pace'] * 60)} /km at {x['hr']:.0f} bpm" for x in easy_runs) or "none yet"
    nike.append(check("P1", "Easy days stay easy", res, f"{len(easy_dates)} easy days: {detail}."))
    longs = [x for x in runs if x["dur"] >= c["long_run_min"]]
    nike.append(check("P1", "One longer run", "pass" if longs else "fail",
                      f"Longest run is {max((x['dur'] for x in runs), default=0):.0f} min. The mark is {c['long_run_min']} min."))
    if "fivek" in band:
        mins = week.get("fivek") or 0
        target = plan["bridge_target_min"]
        nike.append(check("P2", "Minutes at today's 5K pace", "pass" if mins >= target else "partial" if mins >= target / 4 else "fail",
                          f"{mins:.1f} min in {band['fivek']['name'].split(', ')[1]} /km. Week 1 asks for about {target} min."))
    goal, mile = week.get("goal") or 0, week.get("mile") or 0
    over = (goal > plan["goal_pace_cap_min"]) + (mile > plan["fastest_cap_min"])
    nike.append(check("P3", "Goal pace stays a short set", ["pass", "partial", "fail"][over],
                      f"{goal:.1f} min at goal 5K pace (cap {plan['goal_pace_cap_min']}) and {mile:.1f} min at sub-7 mile pace (cap {plan['fastest_cap_min']})."))

    cz = []
    vo2 = next((x["v"] for x in reversed(p["vo2"]) if x["v"] is not None), None)
    need = 2 if vo2 is None or vo2 < c["vo2_two_speed_days_below"] else 3
    res = "pass" if n == need else "partial" if n else "fail"
    vtext = f"VO2 estimate is {vo2:.1f}" if vo2 is not None else "No VO2 estimate"
    cz.append(check("C12", f"{need} speed days", res, f"{vtext}, so {need} speed days. This week has {n}."))
    if speed:
        inside = week.get("speed") or 0
        fast = week.get("fast") or 0
        share = inside / fast if fast else 0
        res = "pass" if share >= 0.5 else "partial" if share >= 0.25 else "fail"
        cz.append(check("C11", "Speed sits at 3K–10K pace now", res,
                        f"{inside:.1f} of {fast:.1f} fast minutes are at {mmss(speed['lo'])}–{mmss(speed['hi'])} /km ({share * 100:.0f}%). Faster work is goal-pace practice."))
    if peak:
        floor = peak[1] * c["speed_volume_floor"]
        hours = week["runH"] or 0
        pace = hours * 7 / elapsed
        if hours >= floor:
            res, text = "pass", f"{hours:.1f} h this week. The floor is {floor:.1f} h."
        elif week["partial"] and pace >= floor:
            res, text = "partial", f"{hours:.1f} h after {elapsed} days, on pace for {pace:.1f} h. The floor is {floor:.1f} h."
        else:
            res, text = "fail", f"{hours:.1f} h after {elapsed} days. The floor is {floor:.1f} h."
        cz.append(check("C13", "Volume stays near the peak", res,
                        text + f" Peak is {peak[1]:.1f} h a week, the 4 weeks from {peak[0]}; floor is {c['speed_volume_floor'] * 100:.0f}%."))
    long_dec = week.get("longDec")
    if long_dec is None:
        cz.append(check("C2", "Long run stays durable", "na", f"No run of {c['long_run_min']} min or more with a decoupling value this week."))
    else:
        cz.append(check("C2", "Long run stays durable", "pass" if abs(long_dec) <= c["decoupling_limit"] else "fail",
                        f"Longest run decouples {long_dec:.1f}%. The limit is ±{c['decoupling_limit']}%."))
    mornings = [readiness(p, cfg, journal, date.fromisoformat(d)) for d in sorted({x["date"] for x in speed_runs})]
    if not mornings:
        cz.append(check("C17", "Speed on ready mornings", "na", "No speed day yet this week."))
    else:
        worst = min(LEVELS.index(m["level"]) for m in mornings)
        res = "fail" if worst <= 1 else "partial" if any(m["flags"] for m in mornings) else "pass"
        text = "; ".join(f"{m['date']}: {m['text'].lower()}" + (f" ({len(m['flags'])} flag{'s' if len(m['flags']) > 1 else ''})" if m["flags"] else "") for m in mornings)
        cz.append(check("C17", "Speed on ready mornings", res, text + "."))
    return {"week": number, "weekStart": week["week"], "elapsed": elapsed, "nike": nike, "couzens": cz,
            "nikeScore": sum(x["score"] for x in nike), "couzensScore": sum(x["score"] for x in cz),
            "nikeMax": len(nike), "couzensMax": len(cz)}


def recovery_eval(p, cfg):
    c = cfg["couzens"]
    lo, hi = cfg["zones"]["easy_hr"]
    nights = p["recovery"]["nights"]
    sleeps = [n["asleep"] for n in nights]
    joins = {j["id"]: j for j in p["recovery"]["joins"]}
    easy = [x for x in p["runs"] if continuous(x) and x["dur"] >= cfg["filters"]["min_steady_min"] and lo <= x["hr"] <= hi]
    linked = [x for x in easy if x["id"] in joins]
    short, target = c["short_sleep_h"], c["sleep_target_h"]

    def group(rows):
        return {"n": len(rows), "ef": r(med([x["ef"] for x in rows]), 2), "pwr": r(med([x["pwr"] for x in rows]), 0),
                "dec": r(med([x["decP"] for x in rows]), 1), "temp": r(med([x["temp"] for x in rows]), 0),
                "hr": r(med([x["hr"] for x in rows]), 0)}

    sleep_rows = [
        {"label": f"Under {short} h", **group([x for x in linked if joins[x["id"]]["sleep"] < short])},
        {"label": f"{short} to {target} h", **group([x for x in linked if short <= joins[x["id"]]["sleep"] < target])},
        {"label": f"{target} h or more", **group([x for x in linked if joins[x["id"]]["sleep"] >= target])},
    ]
    cool, hot = c["cool_c"], c["hot_c"]
    temps = [x for x in easy if x["temp"] is not None]
    heat_rows = [
        {"label": f"Under {cool}°C", **group([x for x in temps if x["temp"] < cool])},
        {"label": f"{cool} to {hot}°C", **group([x for x in temps if cool <= x["temp"] < hot])},
        {"label": f"{hot}°C or more", **group([x for x in temps if x["temp"] >= hot])},
    ]
    p_hot = med([x["pwr"] for x in temps if x["temp"] >= hot])
    p_rest = med([x["pwr"] for x in temps if x["temp"] < hot])
    return {
        "nights": len(sleeps),
        "median": r(med(sleeps), 1),
        "under": r(100 * sum(s < short for s in sleeps) / len(sleeps), 0) if sleeps else None,
        "over": r(100 * sum(s >= target for s in sleeps) / len(sleeps), 0) if sleeps else None,
        "naps": sum(1 for n in nights if n.get("nap")),
        "rhoEf": r(spearman([joins[x["id"]]["sleep"] for x in linked], [x["ef"] for x in linked]), 2),
        "rhoPwr": r(spearman([joins[x["id"]]["sleep"] for x in linked], [x["pwr"] for x in linked]), 2),
        "nLinked": len(linked),
        "heatDrop": r(p_rest - p_hot, 0) if p_hot is not None and p_rest is not None else None,
        "sleepRows": sleep_rows,
        "heatRows": heat_rows,
    }


def _in_season_hours(cfg, weeks, peak):
    plan = cfg["plan"]
    start = date.fromisoformat(plan["start"])
    end = start + timedelta(weeks=plan["weeks"])
    block = [w["runH"] or 0 for w in weeks if start <= date.fromisoformat(w["week"]) < end and not w["partial"]]
    if block:
        noun = "week" if len(block) == 1 else "weeks"
        return statistics.mean(block), f"{len(block)} full {noun} of this plan"
    if peak:
        return peak[1], f"the 4-week peak from {peak[0]}"
    recent = [w["runH"] or 0 for w in weeks if not w["partial"]][-4:]
    if not recent:
        return None, None
    return statistics.mean(recent), "the last full weeks"


def off_season(cfg, weeks, as_of, peak):
    """Rule C21. Weekly run hours to keep after the plan races."""
    c = cfg["couzens"]
    plan = cfg.get("plan") or {}
    if not plan.get("start") or not plan.get("weeks"):
        return None
    base, label = _in_season_hours(cfg, weeks, peak)
    if base is None or base < 0.05:
        return None
    race_end = date.fromisoformat(plan["start"]) + timedelta(weeks=plan["weeks"])
    shed_weeks = c.get("off_shed_weeks", 4)
    shed_end = race_end + timedelta(weeks=shed_weeks)
    off_end = race_end + timedelta(days=c.get("off_season_days", 60))
    low, high = c.get("off_shed_low", 0.10), c.get("off_shed_high", 0.40)
    hold_share = c.get("off_hold_share", 0.50)
    lo, hi, hold = base * low, base * high, base * hold_share
    if as_of < race_end:
        phase = "before"
    elif as_of < shed_end:
        phase = "shed"
    elif as_of < off_end:
        phase = "hold"
    else:
        phase = "after"
    days_left = (race_end - as_of).days
    if days_left >= 14:
        when = f"in {days_left // 7} weeks"
    elif days_left > 1:
        when = f"in {days_left} days"
    elif days_left == 1:
        when = "in 1 day"
    else:
        when = "now"
    paragraphs = [
        f"The plan ends on {race_end.isoformat()}, {when}. After that, take about {c.get('off_season_days', 60)} days with no specific 5K work. Those days shed the fatigue of the block, including the part you may not feel.",
        f"Your in-season week is {base:.1f} run hours, from {label}. For the first {shed_weeks} weeks, {race_end.isoformat()} to {shed_end.isoformat()}, cut the week to {lo:.1f}–{hi:.1f} run hours. That is a cut of {100 - high * 100:.0f}–{100 - low * 100:.0f}%. Taper studies found that a drop of 60–90% sheds fatigue fastest. Keep each session easy enough that you feel better after it than before it. Easy walks can fill the day. They are general movement.",
        f"From {shed_end.isoformat()} to {off_end.isoformat()}, hold about {hold:.1f} run hours a week, about half of the in-season week. A cut of about 40–50% kept fitness in Couzens' own log, and a cut of about half for about two months is what his athletes do after a key race. Swimmers held performance for 5 weeks at 60% of normal volume. A long block near 20% of normal volume lost fitness. This second block stays easy. Add mobility and short skill reps. Specific 5K work waits until after {off_end.isoformat()}.",
    ]
    if phase == "shed":
        action = f"This is the first off-season block, until {shed_end.isoformat()}. Keep the week at {lo:.1f}–{hi:.1f} run hours, and keep it easy."
    elif phase == "hold":
        action = f"This is the second off-season block, until {off_end.isoformat()}. Hold about {hold:.1f} run hours, still easy, with mobility and short skill reps. Specific 5K work waits until after {off_end.isoformat()}."
    elif phase == "before":
        action = f"After {race_end.isoformat()}, cut weekly run time from {base:.1f} h to {lo:.1f}–{hi:.1f} h for {shed_weeks} weeks, then hold about {hold:.1f} h until {off_end.isoformat()}. Specific 5K work waits until after that date."
    else:
        action = None
    return {
        "phase": phase,
        "raceEnd": race_end.isoformat(),
        "shedEnd": shed_end.isoformat(),
        "offEnd": off_end.isoformat(),
        "baseH": r(base, 2),
        "baseLabel": label,
        "shedLo": r(lo, 2),
        "shedHi": r(hi, 2),
        "holdH": r(hold, 2),
        "action": action,
        "paragraphs": paragraphs,
    }


def actions(cfg, base, plan, ready, rec, weeks, bands, off):
    c = cfg["couzens"]
    out = []
    if ready["flags"] or ready["level"] in ("recovery", "rest"):
        reasons = " ".join(f["text"] for f in ready["flags"])
        out.append({"rule": "C17", "text": f"Today reads as a {ready['text'].lower()}. {reasons} Keep the day easy and move a key session to a better morning."})
    if plan:
        for x in plan["couzens"]:
            if x["rule"] == "C13" and x["result"] != "pass":
                out.append({"rule": "C13", "text": "Bring the week up to the volume floor with easy running. " + x["evidence"]})
            if x["rule"] == "C11" and x["result"] != "pass":
                speed = next(b for b in bands if b["key"] == "speed")
                out.append({"rule": "C11", "text": f"Put the bulk of the next speed day at {mmss(speed['lo'])}–{mmss(speed['hi'])} per km, your 3K–10K pace now. Aim for {cfg['plan']['bridge_target_min']} minutes there before any goal-pace reps."})
        p3 = next((x for x in plan["nike"] if x["rule"] == "P3"), None)
        if p3 and p3["result"] != "pass":
            out.append({"rule": "P3", "text": f"Stop goal-pace reps at {cfg['plan']['goal_pace_cap_min']} minutes and sub-7 reps at {cfg['plan']['fastest_cap_min']} minute. " + p3["evidence"]})
    full = [w for w in weeks if not w["partial"] and w["longDec"] is not None][-2:]
    if len(full) == 2 and all(abs(w["longDec"]) > c["decoupling_limit"] for w in full):
        out.append({"rule": "C2", "text": f"The last two long runs decoupled {full[0]['longDec']:.1f}% and {full[1]['longDec']:.1f}%. Drop one speed day this week and recover (C18)."})
    for x in base:
        if x["rule"] == "C8" and x["result"] == "fail":
            out.append({"rule": "C8", "text": "Monthly run time is down. Volume is the base dose; add easy time before more intensity."})
        if x["rule"] == "C6" and x["result"] != "pass":
            out.append({"rule": "C6", "text": "Too much of the week is hard. Move sessions back under the hard line until most time is easy."})
    if rec["under"] is not None and rec["under"] >= 30:
        out.append({"rule": "C17", "text": f"{rec['under']:.0f}% of nights are under {c['short_sleep_h']} h. Aim for {c['sleep_target_h']} h before speed mornings and long runs."})
    if rec["heatDrop"] is not None and rec["heatDrop"] >= 3:
        out.append({"rule": "C5", "text": f"Easy runs at {c['hot_c']}°C or more give up about {rec['heatDrop']:.0f} W. Keep hot runs easy and put speed on cooler mornings."})
    if off and off.get("action"):
        out.append({"rule": "C21", "text": off["action"]})
    return out


def evaluate(p, cfg, journal):
    as_of = date.fromisoformat(p["asOfDate"])
    rows, five, before, after = records(p, cfg, journal)
    bands = derived_bands(cfg, five)
    weeks = weeks_table(p, cfg, bands, as_of)
    peak = peak_volume(weeks, cfg)
    entry_days = [e["date"] for e in journal.get("entries", []) if e.get("date")]
    day = max([as_of] + [date.fromisoformat(d) for d in entry_days if d <= (as_of + timedelta(days=1)).isoformat()])
    ready = readiness(p, cfg, journal, day)
    base = base_checks(p, cfg, weeks, before, after, as_of)
    plan = plan_checks(p, cfg, journal, weeks, bands, as_of, peak)
    rec = recovery_eval(p, cfg)
    off = off_season(cfg, weeks, as_of, peak)
    new_runs = []
    prev = (p.get("sync") or {}).get("previousLocal")
    if prev:
        new_runs = [x["id"] for x in p["runs"] if f"{x['date']} {x['start']}" > prev[:16]]
    return {
        "records": rows,
        "fiveK": {"paceS": r(five["paceS"], 1), "date": five["date"]} if five else None,
        "bands": [{**b, "lo": r(b["lo"], 1), "hi": r(b["hi"], 1)} for b in bands],
        "weeks": weeks,
        "peak": {"from": peak[0], "hours": r(peak[1], 2)} if peak else None,
        "readiness": ready,
        "base": base,
        "baseScore": sum(x["score"] for x in base),
        "baseMax": len(base),
        "plan": plan,
        "recovery": rec,
        "fatigue": fatigue_curve(p.get("mmp") or []),
        "offSeason": off,
        "actions": actions(cfg, base, plan, ready, rec, weeks, bands, off),
        "newRuns": new_runs,
        "rules": [{"id": k, "text": v[0], "url": v[1]} for k, v in RULES.items()],
    }


def summary(p):
    e = p["eval"]
    current = e["weeks"][-1] if e["weeks"] else {}
    return {
        "asOf": p["asOf"],
        "baseScore": e["baseScore"],
        "planWeek": e["plan"]["week"] if e["plan"] else None,
        "nikeScore": e["plan"]["nikeScore"] if e["plan"] else None,
        "couzensScore": e["plan"]["couzensScore"] if e["plan"] else None,
        "readiness": e["readiness"]["level"],
        "records": {x["name"]: x["time"] for x in e["records"]},
        "week": current,
        "checks": {f"{x['rule']} {x['name']}": x["result"] for x in e["base"] + ((e["plan"] or {}).get("nike", []) + (e["plan"] or {}).get("couzens", []))},
    }
