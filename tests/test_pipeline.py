import json
import re
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True

import couzens  # noqa: E402
import health_payload as hp  # noqa: E402
import sync_server  # noqa: E402

WATCH = 'sourceName="Test Apple\u00a0Watch" device="&lt;&lt;HKDevice&gt;, hardware:Watch7,8, software:11&gt;"'
T0 = datetime(2026, 9, 29, 7, 0, 0)


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S +0100")


def stat(kind, start, end, **values):
    extra = " ".join(f'{k}="{v}"' for k, v in values.items())
    return f'<WorkoutStatistics type="HKQuantityTypeIdentifier{kind}" startDate="{stamp(start)}" endDate="{stamp(end)}" {extra}/>'


def record(kind, at, value, unit="count/min"):
    return f'<Record type="HKQuantityTypeIdentifier{kind}" {WATCH} unit="{unit}" startDate="{stamp(at)}" endDate="{stamp(at)}" value="{value}"/>'


def sleep(start, end, value="AsleepCore"):
    return f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" {WATCH} startDate="{stamp(start)}" endDate="{stamp(end)}" value="HKCategoryValueSleepAnalysis{value}"/>'


def fixture_xml():
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<HealthData>"]
    start, end = T0, T0 + timedelta(minutes=50)
    mid = T0 + timedelta(minutes=25)
    lines.append(f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="50" durationUnit="min" {WATCH} startDate="{stamp(start)}" endDate="{stamp(end)}">')
    lines.append('<MetadataEntry key="HKIndoorWorkout" value="0"/>')
    lines.append(stat("DistanceWalkingRunning", start, end, sum="5.5", unit="km"))
    lines.append(stat("DistanceWalkingRunning", start, mid, sum="2.7", unit="km"))
    lines.append(stat("DistanceWalkingRunning", mid, end, sum="2.8", unit="km"))
    lines.append(stat("HeartRate", start, end, average="140", unit="count/min"))
    lines.append(stat("RunningPower", start, end, average="110", unit="W"))
    lines.append(stat("RunningSpeed", start, end, average="6.6", unit="km/hr"))
    lines.append("</Workout>")
    lines.append(f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="49" durationUnit="min" sourceName="Nike Run Club" startDate="{stamp(start + timedelta(minutes=1))}" endDate="{stamp(end)}">')
    lines.append(stat("DistanceWalkingRunning", start, end, sum="5.6", unit="km"))
    lines.append("</Workout>")
    iw = T0 + timedelta(hours=2)
    ie = iw + timedelta(minutes=15)
    lines.append(f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="15" durationUnit="min" {WATCH} startDate="{stamp(iw)}" endDate="{stamp(ie)}">')
    lines.append(stat("DistanceWalkingRunning", iw, ie, sum="1.44", unit="km"))
    lines.append(stat("HeartRate", iw, ie, average="135", unit="count/min"))
    lines.append(stat("RunningPower", iw, ie, average="95", unit="W"))
    lines.append(stat("RunningSpeed", iw, ie, average="5.5", unit="km/hr"))

    def step(a, b, dur, dist, hr, hr_min, spd, pwr):
        return "\n".join([
            f'<WorkoutActivity startDate="{stamp(a)}" endDate="{stamp(b)}" duration="{dur}" durationUnit="min">',
            stat("DistanceWalkingRunning", a, b, sum=dist, unit="km"),
            stat("HeartRate", a, b, average=hr, minimum=hr_min, unit="count/min"),
            stat("RunningSpeed", a, b, average=spd, unit="km/hr"),
            stat("RunningPower", a, b, average=pwr, unit="W"),
            "</WorkoutActivity>",
        ])

    lines.append(step(iw, ie, 15, "1.44", "135", "100", "5.5", "90"))
    cursor = iw
    for _ in range(3):
        run_end = cursor + timedelta(minutes=4)
        lines.append(step(cursor, run_end, 4, "0.40", "140", "120", "6", "100"))
        walk_end = run_end + timedelta(minutes=1)
        lines.append(step(run_end, walk_end, 1, "0.08", "125", "110", "4.8", "70"))
        cursor = walk_end
    lines.append("</Workout>")
    for _ in range(2):
        s = T0 + timedelta(hours=10)
        lines.append(f'<Workout workoutActivityType="HKWorkoutActivityTypeHighIntensityIntervalTraining" duration="20" durationUnit="min" sourceName="Sworkit" startDate="{stamp(s)}" endDate="{stamp(s + timedelta(minutes=20))}">')
        lines.append("</Workout>")
    t = start
    while t < end:
        moving = not (mid - timedelta(minutes=3) <= t < mid + timedelta(minutes=3))
        lines.append(record("HeartRate", t, 140 if moving else 100))
        if moving:
            lines.append(record("RunningPower", t, 110, "W"))
            lines.append(record("RunningSpeed", t, 6.6, "km/hr"))
        t += timedelta(seconds=10)
    lines.append(sleep(T0 - timedelta(hours=8), T0 - timedelta(hours=7)))
    lines.append(sleep(T0 - timedelta(hours=7), T0 - timedelta(hours=1)))
    lines.append(sleep(T0 + timedelta(hours=7), T0 + timedelta(hours=8)))
    lines.append("</HealthData>")
    return "\n".join(lines) + "\n"


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.zip = Path(cls.tmp.name) / "export.zip"
        with zipfile.ZipFile(cls.zip, "w") as z:
            z.writestr(hp.XML, fixture_xml())
        cls.cfg = hp.load_config()
        cls.cfg["weeks_from"] = "2026-09-21"
        cls.cfg["load_from"] = "2026-09-21"
        cls.payload = hp.build_payload(cls.zip, cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_full_span_statistic_wins_over_laps(self):
        self.assertAlmostEqual(self.payload["runs"][0]["dist"], 5.5)

    def test_copies_and_duplicates_are_dropped(self):
        self.assertEqual(self.payload["dropped"], {"duplicate": 1, "overlap:Nike Run Club": 1})
        self.assertEqual(len(self.payload["allRuns"]), 2)

    def test_run_walk_is_separate_from_steady(self):
        walks = self.payload["runWalks"]
        self.assertEqual(len(walks), 1)
        self.assertEqual(walks[0]["kind"], "4-1")
        self.assertEqual(walks[0]["n"], 3)
        self.assertAlmostEqual(walks[0]["rec"], 30)
        self.assertAlmostEqual(walks[0]["runDist"], 0.4)
        self.assertIsNone(self.payload["runs"][0].get("runWalk"))
        marked = next(r for r in self.payload["runs"] if r.get("runWalk"))
        self.assertEqual(marked["runWalk"], "4-1")
        self.assertFalse(marked["steady"])
        strides = []
        for _ in range(6):
            strides.append({"dur": 0.5, "dist": 0.1, "hr": 160, "hr_min": 150, "spd": 12, "pwr": 200})
            strides.append({"dur": 1.0, "dist": 0.08, "hr": 120, "hr_min": 100, "spd": 5, "pwr": 80})
        self.assertIsNone(hp.classify_run_walk(strides, 9))

    def test_best_efforts_skip_run_walk(self):
        start = datetime(2026, 9, 29, 8, 0, 0)
        end = start + timedelta(minutes=10)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<HealthData>"]
        lines.append(f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="10" durationUnit="min" {WATCH} startDate="{stamp(start)}" endDate="{stamp(end)}">')
        lines.append(stat("HeartRate", start, end, average="140", unit="count/min"))
        lines.append(stat("RunningPower", start, end, average="140", unit="W"))
        lines.append(stat("RunningSpeed", start, end, average="8", unit="km/hr"))
        lines.append("</Workout>")
        t = start
        while t < end:
            lines.append(record("HeartRate", t, 140))
            lines.append(record("RunningPower", t, 140, "W"))
            lines.append(record("RunningSpeed", t, 8, "km/hr"))
            t += timedelta(seconds=10)
        rw = start + timedelta(hours=3)
        lines.append(f'<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="15" durationUnit="min" {WATCH} startDate="{stamp(rw)}" endDate="{stamp(rw + timedelta(minutes=15))}">')

        def piece(a, b, dur, hr, spd, pwr):
            return "\n".join([
                f'<WorkoutActivity startDate="{stamp(a)}" endDate="{stamp(b)}" duration="{dur}" durationUnit="min">',
                stat("HeartRate", a, b, average=hr, minimum=hr, unit="count/min"),
                stat("RunningSpeed", a, b, average=spd, unit="km/hr"),
                stat("RunningPower", a, b, average=pwr, unit="W"),
                "</WorkoutActivity>",
            ])

        cursor = rw
        for _ in range(3):
            run_end = cursor + timedelta(minutes=4)
            lines.append(piece(cursor, run_end, 4, 170, 16, 280))
            walk_end = run_end + timedelta(minutes=1)
            lines.append(piece(run_end, walk_end, 1, 120, 5, 80))
            cursor = walk_end
        lines.append("</Workout>")
        while rw < cursor:
            lines.append(record("HeartRate", rw, 170))
            lines.append(record("RunningPower", rw, 280, "W"))
            lines.append(record("RunningSpeed", rw, 16, "km/hr"))
            rw += timedelta(seconds=10)
        lines.append("</HealthData>")
        folder = Path(self.tmp.name) / "mmp"
        folder.mkdir()
        zpath = folder / "export.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr(hp.XML, "\n".join(lines))
        payload = hp.build_payload(zpath, self.cfg)
        self.assertEqual(len(payload["runWalks"]), 1)
        one = next(row for row in payload["mmp"] if row["min"] == 1)
        self.assertLess(one["pwr"], 200)
        self.assertLess(one["spd"], 12)

    def test_night_spans_midnight_and_nap_is_separate(self):
        nights = {n["date"]: n for n in self.payload["recovery"]["nights"]}
        self.assertEqual(nights["2026-09-29"]["asleep"], 7.0)
        self.assertEqual(nights["2026-09-29"]["nap"], 1.0)

    def test_decoupling_ignores_heart_rate_while_stopped(self):
        run = self.payload["runs"][0]
        self.assertGreater(run["hr1"], 139)
        self.assertGreater(run["hr2"], 139)
        self.assertLess(abs(run["decP"]), 1)

    def test_as_of_comes_from_data(self):
        self.assertEqual(self.payload["asOfDate"], "2026-09-29")
        self.assertEqual(hp.latest_end(self.zip)[0], "2026-09-29T16:20:00Z")

    def test_build_is_deterministic(self):
        again = hp.build_payload(self.zip, self.cfg)
        self.assertEqual(json.dumps(self.payload, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_evaluate_runs_and_names_rules(self):
        e = couzens.evaluate(self.payload, self.cfg, {"entries": [], "races": []})
        self.assertTrue(all(c["rule"] in couzens.RULES for c in e["base"]))
        self.assertEqual(e["records"][2]["name"], "5 km")
        self.assertEqual(e["records"][2]["date"], "2026-09-29")
        self.assertGreaterEqual(e["fatigue"]["power"]["n"], 3)
        self.assertIn("C19", couzens.RULES)
        self.assertIn("C20", couzens.RULES)


class ReadinessTest(unittest.TestCase):
    def payload(self, hrv_today, rhr_today, sleep_h):
        day0 = datetime(2026, 6, 1)
        hrv = [{"d": (day0 + timedelta(days=i)).date().isoformat(), "v": 40 + i % 20} for i in range(90)]
        rhr = [{"d": (day0 + timedelta(days=i)).date().isoformat(), "v": 60} for i in range(90)]
        today = (day0 + timedelta(days=90)).date().isoformat()
        hrv.append({"d": today, "v": hrv_today})
        rhr.append({"d": today, "v": rhr_today})
        return {"hrv": hrv, "rhr": rhr, "runs": [], "recovery": {"nights": [{"date": today, "asleep": sleep_h}]}}, today

    def level(self, *args, journal=None):
        p, today = self.payload(*args)
        cfg = hp.load_config()
        return couzens.readiness(p, cfg, journal or {}, datetime.fromisoformat(today).date())

    def test_high_hrv_good_markers_is_key_day(self):
        self.assertEqual(self.level(70, 60, 8)["level"], "intensity")

    def test_each_flag_lowers_a_level(self):
        self.assertEqual(self.level(70, 66, 8)["level"], "aerobic")

    def test_two_flags_cap_at_recovery(self):
        self.assertEqual(self.level(70, 66, 5)["level"], "recovery")

    def test_journal_scores_flag(self):
        p, today = self.payload(50, 60, 8)
        r = couzens.readiness(p, hp.load_config(), {"entries": [{"date": today, "soreness": 2}]}, datetime.fromisoformat(today).date())
        self.assertEqual(r["level"], "recovery")


class FatigueTest(unittest.TestCase):
    def test_index_of_minus_0_10_fades_about_6_7_percent(self):
        mmp = []
        for minutes in (5, 20, 60):
            hours = minutes / 60
            mmp.append({"min": minutes, "pwr": 263 * hours ** -0.10, "spd": 16 * hours ** -0.07, "pace": 60 / (16 * hours ** -0.07)})
        fit = couzens.fatigue_curve(mmp)
        self.assertAlmostEqual(fit["power"]["index"], -0.10, places=2)
        self.assertAlmostEqual(fit["power"]["fade"], 6.7, delta=0.05)
        self.assertAlmostEqual(fit["power"]["hourW"], 263, places=0)
        self.assertAlmostEqual(fit["speed"]["fade"], (1 - 2 ** -0.07) * 100, delta=0.1)

    def test_two_points_do_not_fit(self):
        self.assertIsNone(couzens.fatigue_curve([
            {"min": 5, "pwr": 300, "spd": 16},
            {"min": 20, "pwr": 250, "spd": 14},
        ]))


class OffSeasonTest(unittest.TestCase):
    def weeks(self):
        start = datetime(2026, 9, 28).date()
        return [{"week": (start + timedelta(weeks=i)).isoformat(), "partial": False, "runH": 4.0} for i in range(3)]

    def test_before_the_race_cuts_to_10_to_40_percent_then_half(self):
        off = couzens.off_season(hp.load_config(), self.weeks(), datetime(2026, 10, 5).date(), None)
        self.assertEqual(off["phase"], "before")
        self.assertEqual(off["raceEnd"], "2026-11-23")
        self.assertAlmostEqual(off["baseH"], 4.0)
        self.assertAlmostEqual(off["shedLo"], 0.4)
        self.assertAlmostEqual(off["shedHi"], 1.6)
        self.assertAlmostEqual(off["holdH"], 2.0)
        self.assertEqual(off["offEnd"], "2027-01-22")
        self.assertIn("0.4–1.6 h", off["action"])
        self.assertIn("2.0 h", off["action"])
        self.assertIn("C21", couzens.RULES)

    def test_shed_phase_names_the_current_cap(self):
        off = couzens.off_season(hp.load_config(), self.weeks(), datetime(2026, 11, 25).date(), None)
        self.assertEqual(off["phase"], "shed")
        self.assertIn("0.4", off["action"])
        self.assertIn("1.6", off["action"])


class SectionToggleTest(unittest.TestCase):
    def setUp(self):
        self.html = Path("report_template.html").read_text()

    def test_each_extra_has_a_page_control_and_a_sidebar_control(self):
        extras = set(re.findall(r'id="extra-([^"]+)"', self.html))
        page = set(re.findall(r'class="more-btn"[^>]*data-extra="([^"]+)"', self.html))
        side = set(re.findall(r'class="nav-toggle"[^>]*data-extra="([^"]+)"', self.html))
        self.assertEqual(extras, page)
        self.assertEqual(extras, side)
        self.assertIn("trend", extras)
        self.assertNotIn("checkin", extras)

    def test_weekly_check_in_stays_open(self):
        for section in ("checkin", "journal", "improve", "progress"):
            block = self.html.split(f'id="{section}"', 1)[1].split("<h2", 1)[0]
            self.assertNotIn("more-btn", block)
            self.assertNotIn("extra-", block)


class JournalTest(unittest.TestCase):
    def test_valid_entry(self):
        entry, race = sync_server.clean_entry({"date": "2026-10-02", "mood": "4", "race": {"km": 5, "time": "27:30"}})
        self.assertEqual(entry, {"date": "2026-10-02", "mood": 4})
        self.assertEqual(race["time"], "27:30")

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            sync_server.clean_entry({"date": "2026-10-02", "mood": 9})
        with self.assertRaises(ValueError):
            sync_server.clean_entry({"date": "yesterday"})


if __name__ == "__main__":
    unittest.main()
