"""Keeps data.json in step with the phone app and schedules boss alerts.

Runs from GitHub Actions:
- pulls what the app posted to ntfy (<topic>-sync) in the last 24 h and merges it into data.json
- for every kill not yet notified whose window is still ahead, schedules an ntfy alert
  5 minutes before the window opens
"""
import json, time, urllib.request, urllib.parse

ALERT_MIN = 10
BOSSES = {
    "reina": ("Reina de las Nieves", "Tempteroon", 83, 97),
    "neoreina": ("Neo Reina de las Nieves", "Tempteroon", 833, 847),
    "barbaro": ("Bárbaro", "Tempteroon", 300, 300),
    "volcano": ("Volcano", "Lupinel", 83, 97),
    "neovolcano": ("Neo Volcano", "Lupinel", 833, 847),
    "sneekie": ("Sneekie", "Lupinel", 300, 300),
    "slobreath": ("Slobreath", "Berneo", 83, 97),
    "frezzard": ("Frezzard", "Berneo", 293, 307),
    "osobuho": ("Oso Búho", "Berneo", 300, 300),
    "haditas": ("Haditas", "Otros", 150, 150),
}
ISLA = ["13:50", "16:50", "19:50", "22:50", "01:50"]

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
CL = ZoneInfo("America/Santiago")

def hm(ms):
    return datetime.fromtimestamp(ms / 1000, CL).strftime("%H:%M")

data = json.load(open("data.json", encoding="utf-8"))
before = json.dumps(data, sort_keys=True)
topic = data["topic"]
deleted = set(data.get("deleted", []))

# 1. merge what the app sent
url = f"https://ntfy.sh/{topic}-sync/json?poll=1&since=24h"
try:
    lines = urllib.request.urlopen(url, timeout=30).read().decode().splitlines()
except Exception as e:
    print("ntfy poll failed:", e)
    lines = []
for line in lines:
    try:
        m = json.loads(line)
        if m.get("event") != "message":
            continue
        msg = json.loads(m["message"])
    except Exception:
        continue
    col = msg.get("col")
    if col not in ("events", "prices"):
        continue
    if msg.get("t") == "add":
        doc = msg.get("doc") or {}
        if not doc.get("id") or doc["id"] in deleted:
            continue
        if not any(d["id"] == doc["id"] for d in data[col]):
            data[col].append(doc)
    elif msg.get("t") == "del":
        deleted.add(msg.get("id"))

for col in ("events", "prices"):
    data[col] = sorted([d for d in data[col] if d["id"] not in deleted], key=lambda d: d["at"])
data["deleted"] = sorted(deleted)[-500:]

# 2. schedule alerts for kills nobody has scheduled yet
now = time.time() * 1000
for ev in data["events"]:
    if ev.get("kind") != "kill" or ev.get("notified") or ev.get("boss") not in BOSSES:
        continue
    name, mapname, mn, mx = BOSSES[ev["boss"]]
    start, end = ev["at"] + mn * 60000, ev["at"] + mx * 60000
    if end < now:
        ev["notified"] = True
        continue
    when = start - ALERT_MIN * 60000
    win = hm(start) if mn == mx else f"{hm(start)} – {hm(end)}"
    q = {"title": f"{name} sale en {ALERT_MIN} min", "tags": "crossed_swords", "priority": "5"}
    if when - now > 15000:
        q["at"] = str(int(when / 1000))
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}?{urllib.parse.urlencode(q)}",
        data=f"{mapname} · canal {ev['ch']}. Ventana {win}.".encode(),
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        ev["notified"] = True
        print("scheduled", name, ev["ch"], win)
    except Exception as e:
        print("schedule failed:", e)

# 2b. no new report: assume it was killed on time and keep warning about the next ones (estimated)
est = set(data.get("est_sent", []))
last = {}
for ev in data["events"]:
    if ev.get("boss") in BOSSES:
        last[(ev["boss"], ev["ch"])] = ev  # events are sorted by time
for (boss, ch), ev in last.items():
    name, mapname, mn, mx = BOSSES[boss]
    k = ev["at"] + (60000 if ev.get("kind") == "spawn" else 0)
    avg = (mn + mx) / 2 * 60000
    for m in range(2, 21):
        mid = k + m * avg
        when = mid - ALERT_MIN * 60000
        if when < now:
            continue
        if when > now + 3 * 3600000:
            break
        key = f"{boss}|{ch}|{int(k)}|{m}"
        if key in est:
            continue
        q = {"title": f"{name} (estimado) en {ALERT_MIN} min", "tags": "hourglass", "priority": "4", "at": str(int(when / 1000))}
        body = f"{mapname} · canal {ch}. Aprox {hm(mid)}, sin dato nuevo desde {hm(ev['at'])}."
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}?{urllib.parse.urlencode(q)}", data=body.encode(), method="POST"), timeout=30)
            est.add(key)
            print("estimated scheduled", name, ch, hm(mid))
        except Exception as e:
            print("estimate schedule failed:", e)
data["est_sent"] = sorted(est)[-300:]

# test ping requested from the chat
if data.pop("test_ping", None):
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://ntfy.sh/{topic}?{urllib.parse.urlencode({'title': 'Prueba de Karos', 'tags': 'white_check_mark', 'priority': '5'})}",
            data="Si ves esto, los avisos funcionan.".encode(), method="POST"), timeout=30)
        print("test ping sent")
    except Exception as e:
        print("test ping failed:", e)

# 3. Isla Flotante: schedule each slot in the next 4 hours, 10 minutes ahead
done = set(data.get("isla_sent", []))
nowdt = datetime.now(CL)
for d in (0, 1):
    day = (nowdt + timedelta(days=d)).date()
    for s_ in ISLA:
        H, M = map(int, s_.split(":"))
        slot = datetime(day.year, day.month, day.day, H, M, tzinfo=CL)
        key = slot.strftime("%Y-%m-%d %H:%M")
        if key in done or not (nowdt < slot <= nowdt + timedelta(hours=4)):
            continue
        when = slot - timedelta(minutes=10)
        q = {"title": "Isla Flotante en 10 min", "tags": "island", "priority": "5"}
        if (when - nowdt).total_seconds() > 15:
            q["at"] = str(int(when.timestamp()))
        req = urllib.request.Request(f"https://ntfy.sh/{topic}?{urllib.parse.urlencode(q)}",
                                     data=f"Abre a las {s_}.".encode(), method="POST")
        try:
            urllib.request.urlopen(req, timeout=30)
            done.add(key)
            print("isla scheduled", key)
        except Exception as e:
            print("isla schedule failed:", e)
data["isla_sent"] = sorted(done)[-20:]

if json.dumps(data, sort_keys=True) != before:
    json.dump(data, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("data.json updated")
