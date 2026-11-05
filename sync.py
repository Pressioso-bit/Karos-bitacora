"""Keeps data.json in step with the phone app and schedules boss alerts.

Runs from GitHub Actions:
- pulls what the app posted to ntfy (<topic>-sync) in the last 24 h and merges it into data.json
- for every kill not yet notified whose window is still ahead, schedules an ntfy alert
  10 minutes before the window opens; newer reports cancel older pending alerts
"""
import json, time, urllib.request, urllib.parse

ALERT_MIN = 10
BOSSES = {
    "reina": ("Reina de las Nieves", "Tempteroon", 83, 97),
    "neoreina": ("Neo Reina de las Nieves", "Tempteroon", 833, 847),
    "barbaro": ("Bárbaro", "Tempteroon", 300, 300),
    "wyvernoro": ("Wyvern de oro", "Tempteroon", 350, 350),
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
    elif msg.get("t") == "mute" and m.get("time", 0) >= data.get("mute_set_at", 0):
        data["mute_until"] = int(msg.get("until") or 0)
        data["mute_set_at"] = m.get("time", 0)

for col in ("events", "prices"):
    data[col] = sorted([d for d in data[col] if d["id"] not in deleted], key=lambda d: d["at"])
data["deleted"] = sorted(deleted)[-500:]

# 2. alerts per boss and channel, keyed by ntfy sequence IDs so stale ones can be cancelled.
#    When a newer report arrives for a boss/channel, every pending alert from the older one is
#    deleted (ntfy drops a deleted scheduled message) and the chain is rebuilt from the new time:
#    m=1 is the real next window, m>=2 are estimates assuming it is killed on time.
now = time.time() * 1000
seqs = data.get("seqs", {})

def ntfy_post(q, body, seq):
    req = urllib.request.Request(f"https://ntfy.sh/{topic}?{urllib.parse.urlencode(q)}",
                                 data=body.encode(), method="POST", headers={"X-Sequence-ID": seq})
    urllib.request.urlopen(req, timeout=30)

def ntfy_delete(seq):
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{topic}/{seq}", method="DELETE"), timeout=30)
    except Exception as e:
        print("delete failed:", seq, e)

last = {}
for ev in data["events"]:
    if ev.get("boss") in BOSSES:
        last[(ev["boss"], ev["ch"])] = ev  # events are sorted by time
muted = now < data.get("mute_until", 0)
for (boss, ch), ev in last.items():
    name, mapname, mn, mx = BOSSES[boss]
    pair = f"{boss}|{ch}"
    rec = seqs.get(pair) or {"anchor": None, "sent": []}
    if muted:
        for seq in rec["sent"]:
            ntfy_delete(seq)
        if rec["sent"]:
            print("muted: cancelled", name, ch, len(rec["sent"]))
        seqs[pair] = {"anchor": None, "sent": []}
        continue
    if rec["anchor"] != ev["id"]:
        for seq in rec["sent"]:
            ntfy_delete(seq)
        if rec["sent"]:
            print("cancelled", name, ch, len(rec["sent"]))
        rec = {"anchor": ev["id"], "sent": []}
    k = ev["at"]  # spawn and kill are the same moment
    avg = (mn + mx) / 2 * 60000
    half = (mx - mn) / 2 * 60000
    for m in range(1, 21):
        seq = f"{boss}-{ch}-{m}"
        if m == 1:
            start, end = k + mn * 60000, k + mx * 60000
            target = start
        else:
            mid = k + m * avg
            start, end, target = mid - half, mid + half, mid
        when = target - ALERT_MIN * 60000
        if end < now or (m > 1 and when < now):
            continue
        if m > 1 and when > now + 4 * 3600000:
            break
        if m > 1 and target - ev["at"] > 6 * 3600000:
            break  # too long without real news: stop guessing
        if seq in rec["sent"]:
            continue
        if m == 1:
            win = hm(start) if mn == mx else f"{hm(start)} – {hm(end)}"
            q = {"title": f"{name} sale en {ALERT_MIN} min", "tags": "crossed_swords", "priority": "5"}
            body = f"{mapname} · canal {ch}. Ventana {win}."
        else:
            q = {"title": f"{name} (estimado) en {ALERT_MIN} min", "tags": "hourglass", "priority": "4"}
            body = f"{mapname} · canal {ch}. Aprox {hm(target)}, sin dato nuevo desde {hm(ev['at'])}."
        if when - now > 15000:
            q["at"] = str(int(when / 1000))
        try:
            ntfy_post(q, body, seq)
            rec["sent"].append(seq)
            print("scheduled" if m == 1 else "estimated scheduled", name, ch, hm(target))
        except Exception as e:
            print("schedule failed:", e)
    # drop estimates already queued for an old chain that is now past the 6 h limit
    keep = []
    for seq in rec["sent"]:
        m_ = int(seq.rsplit("-", 1)[1])
        if m_ > 1 and k + m_ * avg - ev["at"] > 6 * 3600000:
            ntfy_delete(seq)
            print("dropped stale estimate", seq)
        else:
            keep.append(seq)
    rec["sent"] = keep
    seqs[pair] = rec
data["seqs"] = seqs
data.pop("est_sent", None)

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
        seq_isla = "isla-" + slot.strftime("%Y%m%d%H%M")
        if muted:
            if key in done:
                ntfy_delete(seq_isla)
                done.discard(key)
            continue
        if key in done or not (nowdt < slot <= nowdt + timedelta(hours=24)):
            continue
        when = slot - timedelta(minutes=10)
        q = {"title": "Isla Flotante en 10 min", "tags": "island", "priority": "5"}
        if (when - nowdt).total_seconds() > 15:
            q["at"] = str(int(when.timestamp()))
        req = urllib.request.Request(f"https://ntfy.sh/{topic}?{urllib.parse.urlencode(q)}",
                                     data=f"Abre a las {s_}.".encode(), method="POST",
                                     headers={"X-Sequence-ID": seq_isla})
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
