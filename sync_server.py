#!/usr/bin/env python3
"""Serve the report, watch for new Health exports, and keep a journal.

    python3 sync_server.py            serve on http://127.0.0.1:8765/
    python3 sync_server.py --build    rebuild once and exit (add --force to ignore the watermark)
"""

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import couzens
import health_payload

HERE = Path(__file__).resolve().parent
TAG = '<script type="application/json" id="payload">'
PLACEHOLDER = TAG + "{}</script>"
LOCK = threading.Lock()
STATUS = {"state": "idle", "message": "", "buildId": None}
JOURNAL_KEYS = ("mood", "soreness", "fatigue", "stress", "sleepQuality")


def config():
    return health_payload.load_config(HERE / "config.json")


def path_of(cfg, key):
    return (HERE / cfg["paths"][key]).resolve()


def newest_zip(cfg):
    folder = path_of(cfg, "export_dir")
    found = []
    for p in folder.glob("*.zip"):
        try:
            found.append((p.stat().st_mtime, p.name, p))
        except OSError:
            continue
    return max(found)[2] if found else None


def zip_signature(path):
    st = path.stat()
    return (str(path.resolve()), st.st_size, st.st_mtime_ns)


def build_key(cfg):
    digest = hashlib.sha256()
    for name in ("config.json", "health_payload.py", "couzens.py", cfg["paths"]["template"]):
        digest.update((HERE / name).read_bytes())
    return digest.hexdigest()[:16]


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_payload(report):
    html = report.read_text(encoding="utf-8")
    start = html.index(TAG) + len(TAG)
    return json.loads(html[start:html.index("</script>", start)])


def render(cfg, payload):
    template = path_of(cfg, "template").read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise ValueError("The template has no empty payload script.")
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True).replace("<", "\\u003c")
    return template.replace(PLACEHOLDER, TAG + data + "</script>")


def load_journal(cfg):
    data = read_json(path_of(cfg, "journal"), {})
    return {"entries": data.get("entries", []), "races": data.get("races", [])}


def write_metrics(cfg, payload):
    folder = path_of(cfg, "metrics_dir")
    weeks = payload["eval"]["weeks"]
    if weeks:
        out = io.StringIO()
        fields = list(weeks[0].keys())
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(weeks)
        write_atomic(folder / "weeks.csv", out.getvalue())
    write_atomic(folder / "summary.json", json.dumps(couzens.summary(payload), indent=2, sort_keys=True) + "\n")


def publish(cfg, payload):
    journal = load_journal(cfg)
    payload["journal"] = journal
    payload["eval"] = couzens.evaluate(payload, cfg, journal)
    payload.pop("buildId", None)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["buildId"] = hashlib.sha256(body.encode()).hexdigest()[:12]
    write_atomic(path_of(cfg, "report"), render(cfg, payload))
    write_metrics(cfg, payload)
    STATUS["buildId"] = payload["buildId"]
    return payload


def commit(cfg, message):
    if not cfg["server"].get("auto_commit") or not (HERE / ".git").exists():
        return
    paths = [cfg["paths"][k] for k in ("report", "journal", "state", "metrics_dir")]
    paths = [p for p in paths if (HERE / p).exists()]
    try:
        subprocess.run(["git", "add", "--", *paths], cwd=HERE, check=True, capture_output=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE)
        if staged.returncode:
            subprocess.run(["git", "commit", "-m", message], cwd=HERE, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print("git commit skipped:", exc)


def sync(force=False):
    if not LOCK.acquire(blocking=False):
        return {"updated": False, "busy": True, "message": "A sync is already running."}
    try:
        cfg = config()
        STATUS.update(state="syncing", message="Reading the export.")
        path = newest_zip(cfg)
        if not path:
            return {"updated": False, "message": "No zip is in the export folder. Put the Health export zip there."}
        end = health_payload.latest_end(path)
        if not end:
            return {"updated": False, "message": "The zip has no records. Export again from the Health app."}
        latest_utc, latest_local = end
        state_path = path_of(cfg, "state")
        state = read_json(state_path, {})
        key = build_key(cfg)
        report = path_of(cfg, "report")
        same_data = state.get("latestEnd") == latest_utc
        if not force and report.exists() and state.get("buildKey") == key and state.get("latestEnd") and latest_utc <= state["latestEnd"]:
            return {"updated": False, "message": "No new data. The newest record is " + latest_local[:16] + "."}
        STATUS["message"] = "Building the report."
        previous = state.get("previousLocal") if same_data else state.get("latestLocal")
        payload = health_payload.build_payload(path, cfg)
        payload["sync"] = {"zip": path.name, "latestEnd": latest_utc, "latestLocal": latest_local, "previousLocal": previous}
        payload = publish(cfg, payload)
        write_atomic(state_path, json.dumps({
            "zip": path.name,
            "latestEnd": latest_utc,
            "latestLocal": latest_local,
            "previousLocal": previous,
            "buildKey": key,
            "buildId": payload["buildId"],
            "syncedAt": datetime.now().isoformat(timespec="seconds"),
        }, indent=2) + "\n")
        commit(cfg, "Data through " + latest_local[:16])
        fresh = len(payload["eval"]["newRuns"])
        note = f" {fresh} new run{'s' if fresh != 1 else ''}." if previous and not same_data else ""
        return {"updated": True, "message": "Report rebuilt. Data through " + latest_local[:16] + "." + note,
                "buildId": payload["buildId"]}
    finally:
        STATUS.update(state="idle")
        LOCK.release()


def clean_entry(body):
    day = str(body.get("date", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("The date must be YYYY-MM-DD.")
    date.fromisoformat(day)
    entry = {"date": day}
    for key in JOURNAL_KEYS:
        value = body.get(key)
        if value in (None, ""):
            continue
        value = int(value)
        if not 1 <= value <= 5:
            raise ValueError(key + " must be 1 to 5.")
        entry[key] = value
    note = str(body.get("note") or "").strip()[:500]
    if note:
        entry["note"] = note
    race = body.get("race") or {}
    clean_race = None
    if race.get("km") and race.get("time"):
        km = float(race["km"])
        if not 0.2 <= km <= 100:
            raise ValueError("Race distance must be 0.2 to 100 km.")
        if not re.fullmatch(r"(\d+:)?\d{1,2}:\d{2}", str(race["time"]).strip()):
            raise ValueError("Race time must be m:ss or h:mm:ss.")
        clean_race = {"date": day, "km": round(km, 3), "time": str(race["time"]).strip(),
                      "name": str(race.get("name") or "").strip()[:80]}
    return entry, clean_race


def save_journal(body):
    with LOCK:
        cfg = config()
        journal = load_journal(cfg)
        if body.get("delete"):
            day = str(body["delete"])
            journal["entries"] = [e for e in journal["entries"] if e["date"] != day]
            journal["races"] = [x for x in journal["races"] if x["date"] != day]
            message = "Removed the journal entry for " + day + "."
        else:
            entry, race = clean_entry(body)
            journal["entries"] = [e for e in journal["entries"] if e["date"] != entry["date"]] + [entry]
            if race:
                journal["races"] = [x for x in journal["races"] if not (x["date"] == race["date"] and x["km"] == race["km"])] + [race]
            message = "Saved the journal entry for " + entry["date"] + "."
        journal["entries"].sort(key=lambda e: e["date"])
        journal["races"].sort(key=lambda x: (x["date"], x["km"]))
        write_atomic(path_of(cfg, "journal"), json.dumps(journal, indent=2, sort_keys=True) + "\n")
        report = path_of(cfg, "report")
        if report.exists():
            payload = publish(cfg, read_payload(report))
            commit(cfg, "Journal " + (body.get("date") or body.get("delete") or ""))
            return {"ok": True, "message": message, "buildId": payload["buildId"]}
    result = sync(force=True)
    return {"ok": True, "message": message + " " + result["message"], "buildId": result.get("buildId")}


def watch():
    seen = pending = tried = None
    while True:
        try:
            cfg = config()
            path = newest_zip(cfg)
            sig = zip_signature(path) if path else None
            key = build_key(cfg)
            stale = read_json(path_of(cfg, "state"), {}).get("buildKey") != key and key != tried
            if sig and sig != seen and sig != pending:
                pending = sig
            elif sig and (sig == pending or stale):
                result = sync()
                if result.get("busy"):
                    time.sleep(cfg["server"]["watch_seconds"])
                    continue
                tried = key
                STATUS["message"] = result["message"]
                print(time.strftime("%H:%M:%S"), result["message"])
                seen, pending = sig, None
            time.sleep(cfg["server"]["watch_seconds"])
        except Exception as exc:
            STATUS.update(state="error", message="Watch failed: " + str(exc))
            print("watch error:", exc)
            time.sleep(30)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, body):
        self._send(code, json.dumps(body), "application/json")

    def _local(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _trusted_post(self):
        return self._local() and self.headers.get("X-Health-Report") == "1"

    def do_GET(self):
        if not self._local():
            self._send(403, "Forbidden", "text/plain; charset=utf-8")
            return
        route = self.path.split("?")[0]
        cfg = config()
        if route in ("/", "/workout-trends.html"):
            report = path_of(cfg, "report")
            if report.exists():
                self._send(200, report.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(503, '<meta http-equiv="refresh" content="5"><p style="font:16px sans-serif;margin:40px">'
                           + "The report is building. This page reloads by itself. " + STATUS.get("message", "") + "</p>",
                           "text/html; charset=utf-8")
        elif route == "/status":
            self._json(200, STATUS)
        elif route == "/journal":
            self._json(200, load_journal(cfg))
        else:
            self._send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._trusted_post():
            self._send(403, "Forbidden", "text/plain; charset=utf-8")
            return
        route = self.path.split("?")[0]
        try:
            if route == "/sync":
                self._json(200, sync(force="force=1" in self.path))
            elif route == "/journal":
                length = min(int(self.headers.get("Content-Length") or 0), 20000)
                self._json(200, save_journal(json.loads(self.rfile.read(length) or b"{}")))
            else:
                self._send(404, "Not found", "text/plain; charset=utf-8")
        except ValueError as exc:
            self._json(400, {"ok": False, "updated": False, "message": str(exc)})
        except Exception as exc:
            self._json(500, {"ok": False, "updated": False, "message": f"Failed: {type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        if "/status" not in (args[0] if args else ""):
            print(time.strftime("%H:%M:%S"), fmt % args)


def main():
    if "--build" in sys.argv:
        print(sync(force="--force" in sys.argv)["message"])
        return
    cfg = config()
    state = read_json(path_of(cfg, "state"), {})
    STATUS["buildId"] = state.get("buildId")
    if not path_of(cfg, "report").exists() or state.get("buildKey") != build_key(cfg):
        threading.Thread(target=lambda: print(sync()["message"]), daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    host, port = cfg["server"]["host"], cfg["server"]["port"]
    print(f"Report: http://{host}:{port}/")
    print("Export folder:", path_of(cfg, "export_dir"))
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
