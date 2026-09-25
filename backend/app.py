"""CampuSCO backend: Flask + SQLite (no database setup needed).
Run:  pip install -r requirements.txt  &&  python app.py   ->  http://localhost:5000
"""
import os, re, sqlite3, threading, time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE, "..", "frontend"), static_url_path="")
conn = sqlite3.connect(os.path.join(BASE, "campusco.db"), check_same_thread=False)
conn.row_factory = sqlite3.Row
lock = threading.RLock()
DEFAULTS = {"grace": 15, "confirm": 10, "cancel_by": 15, "max": 3, "offset": 0}
DAY0, DAY1 = 9, 17  # bookable hours, 09:00-17:00
FMT = "%Y-%m-%d %H:%M"

class Err(Exception): pass

@app.errorhandler(Err)
def _err(e): return jsonify(error=str(e)), 400

def q(sql, a=()):
    with lock: return [dict(r) for r in conn.execute(sql, a)]
def one(sql, a=()):
    r = q(sql, a); return r[0] if r else None
def x(sql, a=()):
    with lock:
        c = conn.execute(sql, a); conn.commit(); return c.lastrowid

conn.executescript("""
create table if not exists users(
    id integer primary key,
    name text,
    role text,
    avail text default '',
    department text default 'CSE',
    year integer default 3
);
create table if not exists resources(
    id integer primary key,
    name text,
    kind text,
    capacity int,
    location text,
    department text default 'General',
    manager text default 'Campus Admin',
    eligibility text default 'ALL',
    approval_required integer default 0
);
create table if not exists bookings(id integer primary key, user_id int, resource_id int, date text, hour int, status text, purpose text, created text, deadline text, checkin_at text, checkin_method text, noshow_at text, cancelled_at text);
create table if not exists waitlist(id integer primary key, user_id int, resource_id int, date text, hour int, status text, created text, offered_at text, expires text);
create table if not exists notifications(id integer primary key, user_id int, message text, is_read int default 0, created text);
create table if not exists meetings(id integer primary key, student_id int, faculty_id int, date text, hour int, topic text, status text);
create table if not exists student_skills(id integer primary key, user_id int, skill text, level text default 'Intermediate');
create table if not exists settings(k text primary key, v text);
""")

# ---------- problem-statement fields / safe migration ----------
def add_column_if_missing(table, column, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
    except sqlite3.OperationalError:
        pass

add_column_if_missing("users", "department", "text default 'CSE'")
add_column_if_missing("users", "year", "integer default 3")
add_column_if_missing("resources", "department", "text default 'General'")
add_column_if_missing("resources", "manager", "text default 'Campus Admin'")
add_column_if_missing("resources", "eligibility", "text default 'ALL'")
add_column_if_missing("resources", "approval_required", "integer default 0")

# ---------- clock & config ----------
def cfg(k):
    r = one("select v from settings where k=?", (k,)); return int(r["v"]) if r else DEFAULTS[k]
def setcfg(k, v): x("insert or replace into settings values(?,?)", (k, str(int(v))))
def now(): return datetime.now() + timedelta(minutes=cfg("offset"))
def ts(): return now().strftime(FMT)
def dt(s): return datetime.strptime(s, FMT)
def start(d, h): return datetime.strptime(d, "%Y-%m-%d") + timedelta(hours=h)
def end(d, h): return start(d, h) + timedelta(hours=1)
def rname(i): return one("select name from resources where id=?", (i,))["name"]
def uname(i): return one("select name from users where id=?", (i,))["name"]
def notify(uid, msg): x("insert into notifications(user_id,message,created) values(?,?,?)", (uid, msg, ts()))

def seed():
    for t in ("users", "resources", "bookings", "waitlist", "notifications", "meetings", "student_skills"):
        x(f"delete from {t}")

    users_data = [
        ("Pavan", "student", "", "CSE", 3),
        ("Krithi", "student", "", "ISE", 3),
        ("Jahnavi", "student", "", "AIML", 2),
        ("Hithashree", "student", "", "ECE", 3),
        ("Dr. Rao", "faculty", "10,11,14,15", "CSE", 0),
        ("Prof. Nair", "faculty", "9,12,13,16", "ECE", 0),
        ("Admin", "admin", "", "ADMIN", 0)
    ]
    for n, role, avail, dept, year in users_data:
        x("insert into users(name,role,avail,department,year) values(?,?,?,?,?)",
          (n, role, avail, dept, year))

    resources_data = [
        ("AI/ML Laboratory", "lab", 30, "Block A, 2nd floor", "CSE", "Dr. Rao", "CSE,ISE,AIML", 0),
        ("Robotics Lab", "lab", 20, "Block B, ground floor", "ECE", "Prof. Nair", "CSE,ISE,ECE,AIML", 1),
        ("3D Printer Bay", "equipment", 4, "Makerspace", "Innovation Cell", "Mr. Kumar", "ALL", 1),
        ("Library Study Room A", "study", 6, "Library, 1st floor", "Library", "Library Staff", "ALL", 0),
        ("Project Room", "study", 8, "Block C, 3rd floor", "General", "Campus Admin", "ALL", 0),
        ("Quiet Study Zone", "study", 3, "Library, 2nd floor", "Library", "Library Staff", "ALL", 0)
    ]
    for r in resources_data:
        x("""insert into resources
             (name,kind,capacity,location,department,manager,eligibility,approval_required)
             values(?,?,?,?,?,?,?,?)""", r)

    skills = [
        (1, "Python", "Advanced"),
        (1, "Machine Learning", "Intermediate"),
        (1, "Web Development", "Advanced"),
        (2, "UI/UX Design", "Advanced"),
        (2, "Python", "Intermediate"),
        (2, "Machine Learning", "Intermediate"),
        (3, "Marketing", "Advanced"),
        (3, "Digital Marketing", "Advanced"),
        (4, "Arduino", "Advanced"),
        (4, "IoT", "Intermediate"),
        (4, "Robotics", "Advanced")
    ]
    for user_id, skill, level in skills:
        x("insert into student_skills(user_id,skill,level) values(?,?,?)",
          (user_id, skill, level))

if not one("select 1 x from users"): seed()


# ---------- slot logic ----------
def holder(r, d, h): return one("select * from bookings where resource_id=? and date=? and hour=? and status in ('PENDING_APPROVAL','CONFIRMED','CHECKED_IN')", (r, d, h))
def offered(r, d, h): return one("select * from waitlist where resource_id=? and date=? and hour=? and status='OFFERED'", (r, d, h))
def queue(r, d, h): return q("select * from waitlist where resource_id=? and date=? and hour=? and status='WAITING' order by id", (r, d, h))

def promote(r, d, h):
    """Offer a free slot to the first student on the waiting list."""
    if holder(r, d, h) or offered(r, d, h): return
    w = (queue(r, d, h) or [None])[0]
    if not w or end(d, h) <= now(): return
    exp = min(now() + timedelta(minutes=cfg("confirm")), end(d, h))
    x("update waitlist set status='OFFERED',offered_at=?,expires=? where id=?", (ts(), exp.strftime(FMT), w["id"]))
    notify(w["user_id"], f"Good news! The {rname(r)} slot ({d} {h}:00) you were waiting for is now available. Accept within {cfg('confirm')} minutes.")

def sweep():
    """AUTO-RELEASE: no check-in by the grace deadline -> NO_SHOW -> slot freed -> waiting list promoted."""
    n = now()
    for b in q("select * from bookings where status='CONFIRMED'"):
        if n >= dt(b["deadline"]):
            x("update bookings set status='NO_SHOW',noshow_at=? where id=?", (ts(), b["id"]))
            c = one("select count(*) c from bookings where user_id=? and status='NO_SHOW'", (b["user_id"],))["c"]
            tip = " An admin may review your booking privileges." if c >= 3 else " Please cancel bookings you can't attend." if c == 2 else " This is a friendly warning."
            notify(b["user_id"], f"We couldn't detect your check-in. Your {rname(b['resource_id'])} booking ({b['date']} {b['hour']}:00) was released." + tip)
            promote(b["resource_id"], b["date"], b["hour"])
    for b in q("select * from bookings where status='CHECKED_IN'"):
        if n >= end(b["date"], b["hour"]): x("update bookings set status='COMPLETED' where id=?", (b["id"],))
    for w in q("select * from waitlist where status in ('WAITING','OFFERED')"):
        if w["status"] == "OFFERED" and n >= dt(w["expires"]):
            x("update waitlist set status='EXPIRED' where id=?", (w["id"],))
            notify(w["user_id"], f"You didn't accept the {rname(w['resource_id'])} slot in time. It has moved to the next student.")
            promote(w["resource_id"], w["date"], w["hour"])
        elif w["status"] == "WAITING" and n >= end(w["date"], w["hour"]):
            x("update waitlist set status='EXPIRED' where id=?", (w["id"],))

def do_book(uid, r, d, h, purpose, via_wl=False):
    if not DAY0 <= h < DAY1: raise Err("Slots run from 9:00 to 17:00.")
    if end(d, h) <= now(): raise Err("That slot has already ended.")

    user = one("select * from users where id=?", (uid,))
    resource = one("select * from resources where id=?", (r,))
    if not user or not resource:
        raise Err("Student or resource not found.")

    allowed = (resource.get("eligibility") or "ALL").strip()
    if allowed.upper() != "ALL":
        departments = {v.strip().upper() for v in allowed.split(",")}
        if (user.get("department") or "").upper() not in departments:
            raise Err(f"You are not eligible. Eligible departments: {allowed}.")
    if holder(r, d, h) or (offered(r, d, h) and not via_wl): raise Err("Already booked.")
    mine = [b for b in q("select * from bookings where user_id=? and status in ('CONFIRMED','CHECKED_IN')", (uid,)) if end(b["date"], b["hour"]) > now()]
    if len(mine) >= cfg("max"): raise Err(f"You already have {cfg('max')} active bookings, which is the limit.")
    if any(b["date"] == d and b["hour"] == h for b in mine): raise Err("You already have a booking at that time.")
    deadline = (max(start(d, h), now()) + timedelta(minutes=cfg("grace"))).strftime(FMT)
    status = "PENDING_APPROVAL" if resource["approval_required"] and not via_wl else "CONFIRMED"
    bid = x("insert into bookings(user_id,resource_id,date,hour,status,purpose,created,deadline) values(?,?,?,?,?,?,?,?)",
            (uid, r, d, h, status, purpose or "General use", ts(), deadline))

    if status == "PENDING_APPROVAL":
        notify(uid, f"Booking request #{bid} submitted for {rname(r)}. Waiting for resource approval.")
        for a in q("select id from users where role in ('admin','faculty')"):
            notify(a["id"], f"Approval needed: {uname(uid)} requested {rname(r)} on {d} at {h}:00.")
    else:
        notify(uid, f"Booking #{bid} confirmed: {rname(r)}, {d} {h}:00. Check in within {cfg('grace')} minutes of the start or the slot is released.")
    return bid

def next_slot(r):
    for i in range(3):
        d = (now() + timedelta(days=i)).strftime("%Y-%m-%d")
        for h in range(DAY0, DAY1):
            if end(d, h) > now() and not holder(r, d, h) and not offered(r, d, h): return d, h

J = lambda: request.get_json(force=True, silent=True) or {}

@app.before_request
def _sweep():
    if request.path.startswith("/api/"): sweep()

@app.get("/")
def home(): return app.send_static_file("index.html")

# ---------- API ----------
@app.get("/api/users")
def users(): return jsonify(q("select id,name,role,department,year from users"))
@app.get("/api/resources")
def resources(): return jsonify(q("select * from resources"))
@app.get("/api/clock")
def clock(): return jsonify(now=ts(), offset=cfg("offset"), grace=cfg("grace"), confirm=cfg("confirm"), cancel_by=cfg("cancel_by"), max=cfg("max"))

@app.get("/api/slots")
def slots():
    r, d, u = int(request.args["resource"]), request.args["date"], int(request.args.get("user", 0)); out = []
    for h in range(DAY0, DAY1):
        b, o, qu = holder(r, d, h), offered(r, d, h), queue(r, d, h)
        done = one("select 1 x from bookings where resource_id=? and date=? and hour=? and status='COMPLETED'", (r, d, h))
        st = ("PENDING APPROVAL" if b and b["status"] == "PENDING_APPROVAL"
              else "BOOKED" if b else "WAITING LIST" if o else "COMPLETED" if done
              else "PAST" if end(d, h) <= now() else "AVAILABLE")
        out.append(dict(hour=h, status=st, mine=bool(b and b["user_id"] == u), booking_status=b and b["status"], waiting=len(qu),
                        position=next((i + 1 for i, w in enumerate(qu) if w["user_id"] == u), 0),
                        offer=o["id"] if o and o["user_id"] == u else None, expires=o and o["expires"],
                        released=bool(one("select 1 x from bookings where resource_id=? and date=? and hour=? and status in ('NO_SHOW','CANCELLED')", (r, d, h)))))
    return jsonify(out)

@app.post("/api/book")
def book():
    j = J(); return jsonify(booking=do_book(j["user"], j["resource"], j["date"], j["hour"], j.get("purpose")))

@app.post("/api/waitlist")
def wl_join():
    j = J(); r, d, h, u = j["resource"], j["date"], j["hour"], j["user"]
    b = holder(r, d, h)
    if not (b or offered(r, d, h)): raise Err("This slot is free, so you can book it directly.")
    if b and b["user_id"] == u: raise Err("You already hold this slot.")
    if one("select 1 x from waitlist where user_id=? and resource_id=? and date=? and hour=? and status in ('WAITING','OFFERED')", (u, r, d, h)): raise Err("You're already on the waiting list.")
    x("insert into waitlist(user_id,resource_id,date,hour,status,created) values(?,?,?,?,?,?)", (u, r, d, h, "WAITING", ts()))
    pos = len(queue(r, d, h)); notify(u, f"You're #{pos} on the waiting list for {rname(r)}, {d} {h}:00.")
    return jsonify(position=pos)

@app.post("/api/accept")
def accept():
    w = one("select * from waitlist where id=? and status='OFFERED'", (J()["id"],))
    if not w: raise Err("This offer is no longer available.")
    bid = do_book(w["user_id"], w["resource_id"], w["date"], w["hour"], "Waiting-list slot", True)
    x("update waitlist set status='CONFIRMED' where id=?", (w["id"],)); return jsonify(booking=bid)

@app.post("/api/cancel")
def cancel():
    b = one("select * from bookings where id=?", (J()["id"],))
    if not b or b["status"] != "CONFIRMED": raise Err("Only upcoming bookings can be cancelled.")
    if now() > start(b["date"], b["hour"]) - timedelta(minutes=cfg("cancel_by")): raise Err(f"Cancellation closes {cfg('cancel_by')} minutes before the start.")
    x("update bookings set status='CANCELLED',cancelled_at=? where id=?", (ts(), b["id"]))
    notify(b["user_id"], f"Booking #{b['id']} was cancelled."); promote(b["resource_id"], b["date"], b["hour"]); return jsonify(ok=1)

@app.post("/api/checkin")
def checkin():
    j = J(); b = one("select * from bookings where id=?", (j["id"],)); m = j.get("method", "qr")
    if not b or b["status"] != "CONFIRMED": raise Err("This booking can't be checked in.")
    if m != "staff" and now() < start(b["date"], b["hour"]) - timedelta(minutes=15): raise Err("Check-in opens 15 minutes before your slot.")
    x("update bookings set status='CHECKED_IN',checkin_at=?,checkin_method=? where id=?", (ts(), m, b["id"]))
    notify(b["user_id"], f"Check-in successful for {rname(b['resource_id'])}."); return jsonify(ok=1)

@app.post("/api/reschedule")
def reschedule():
    j = J(); b = one("select * from bookings where id=?", (j["id"],)); h = int(j["hour"])
    if not b or b["status"] != "CONFIRMED": raise Err("Only upcoming bookings can be moved.")
    if not DAY0 <= h < DAY1 or end(b["date"], h) <= now(): raise Err("Pick a future hour between 9 and 16.")
    if holder(b["resource_id"], b["date"], h) or offered(b["resource_id"], b["date"], h): raise Err("That slot is already booked. Join its waiting list instead.")
    old = b["hour"]; dl = (start(b["date"], h) + timedelta(minutes=cfg("grace"))).strftime(FMT)
    x("update bookings set hour=?,deadline=? where id=?", (h, dl, b["id"]))
    notify(b["user_id"], f"Booking #{b['id']} moved to {h}:00."); promote(b["resource_id"], b["date"], old); return jsonify(ok=1)

@app.get("/api/my")
def my():
    u = int(request.args["user"])
    w = q("select w.*,r.name rname from waitlist w join resources r on r.id=w.resource_id where user_id=? and status in ('WAITING','OFFERED') order by w.id", (u,))
    for i in w: i["position"] = len([z for z in queue(i["resource_id"], i["date"], i["hour"]) if z["id"] <= i["id"]]) if i["status"] == "WAITING" else 0
    return jsonify(bookings=q("select b.*,r.name rname from bookings b join resources r on r.id=b.resource_id where user_id=? order by date desc,hour desc", (u,)), waitlist=w,
                   notes=q("select * from notifications where user_id=? order by id desc limit 30", (u,)),
                   noshows=one("select count(*) c from bookings where user_id=? and status='NO_SHOW'", (u,))["c"])

@app.post("/api/read")
def read(): x("update notifications set is_read=1 where user_id=?", (J()["user"],)); return jsonify(ok=1)

# ---------- Student Skills ----------
@app.get("/api/skills")
def skills():
    search = request.args.get("search", "").strip().lower()
    if search:
        return jsonify(q("""select s.id,s.skill,s.level,u.id user_id,u.name,u.department
                            from student_skills s join users u on u.id=s.user_id
                            where lower(s.skill) like ? order by s.level desc,s.id""",
                         (f"%{search}%",)))
    return jsonify(q("""select s.id,s.skill,s.level,u.id user_id,u.name,u.department
                        from student_skills s join users u on u.id=s.user_id
                        order by s.skill,s.level desc"""))

@app.post("/api/skill-request")
def skill_request():
    j = J()
    target = j.get("user_id")
    if target:
        notify(int(target), f"{j.get('name','A student')} requested your help with {j.get('skill','a skill')}.")
    return jsonify(ok=1)

# ---------- Resource access approvals ----------
@app.get("/api/admin/approvals")
def approvals():
    return jsonify(q("""select b.id,b.date,b.hour,b.purpose,b.status,
                               u.name student,u.department,
                               r.name resource,r.location,r.manager
                        from bookings b
                        join users u on u.id=b.user_id
                        join resources r on r.id=b.resource_id
                        where b.status='PENDING_APPROVAL'
                        order by b.id desc"""))

@app.post("/api/admin/approval")
def approval():
    j = J()
    b = one("select * from bookings where id=? and status='PENDING_APPROVAL'", (j["id"],))
    if not b:
        raise Err("Approval request not found.")
    if bool(j.get("accept")):
        x("update bookings set status='CONFIRMED' where id=?", (b["id"],))
        notify(b["user_id"], f"Booking #{b['id']} was approved. Check in within {cfg('grace')} minutes of the start.")
    else:
        x("update bookings set status='CANCELLED',cancelled_at=? where id=?", (ts(), b["id"]))
        notify(b["user_id"], f"Booking #{b['id']} was not approved. Please choose another resource or time.")
        promote(b["resource_id"], b["date"], b["hour"])
    return jsonify(ok=1)

# ---------- Faculty Connect ----------
@app.get("/api/faculty")
def faculty():
    d = request.args["date"]; out = []
    for f in q("select * from users where role='faculty'"):
        busy = {m["hour"] for m in q("select hour from meetings where faculty_id=? and date=? and status in ('PENDING','ACCEPTED')", (f["id"], d))}
        out.append(dict(id=f["id"], name=f["name"], slots=[dict(hour=int(h), free=int(h) not in busy) for h in f["avail"].split(",") if h]))
    return jsonify(out)

@app.get("/api/meetings")
def meetings():
    u = int(request.args["user"])
    return jsonify(q("select m.*,s.name student,f.name faculty from meetings m join users s on s.id=m.student_id join users f on f.id=m.faculty_id where student_id=? or faculty_id=? order by m.id desc", (u, u)))

@app.post("/api/meetings")
def meet():
    j = J()
    if one("select 1 x from meetings where faculty_id=? and date=? and hour=? and status in ('PENDING','ACCEPTED')", (j["faculty"], j["date"], j["hour"])): raise Err("That time is already requested.")
    x("insert into meetings(student_id,faculty_id,date,hour,topic,status) values(?,?,?,?,?,'PENDING')", (j["user"], j["faculty"], j["date"], j["hour"], j.get("topic", "")))
    notify(j["faculty"], f"{uname(j['user'])} requested a meeting on {j['date']} at {j['hour']}:00: {j.get('topic', '')}"); return jsonify(ok=1)

@app.post("/api/meetings/respond")
def respond():
    j = J(); m = one("select * from meetings where id=?", (j["id"],)); s = "ACCEPTED" if j["accept"] else "REJECTED"
    x("update meetings set status=? where id=?", (s, m["id"]))
    for u in (m["student_id"], m["faculty_id"]): notify(u, f"Meeting on {m['date']} at {m['hour']}:00 with {uname(m['faculty_id'])} was {s.lower()}.")
    return jsonify(ok=1)

# ---------- AI assistant (rule-based; swap in Gemini/OpenAI here if you like) ----------
@app.post("/api/ai")
def ai():
    t = J().get("text", "").lower()

    if re.search(r"faculty|professor|lecturer|teacher|\\bsir\\b|madam|meeting", t):
        return jsonify(
            reply="This sounds like a faculty-help request. Open Faculty Connect to find available faculty and request a meeting.",
            suggestions=[]
        )

    all_skills = q("""select s.skill,s.level,u.id user_id,u.name,u.department
                      from student_skills s join users u on u.id=s.user_id""")
    for s in all_skills:
        if s["skill"].lower() in t:
            return jsonify(
                reply=f"I found {s['name']} ({s['department']}) with {s['skill']} expertise at {s['level']} level. Open Student Skills to request help.",
                suggestions=[]
            )

    m = re.search(r"(\d+)", t)
    size = int(m.group(1)) if m else 1
    kind = ("equipment" if re.search(r"print|printer|equipment|3d|device", t)
            else "lab" if re.search(r"lab|ai|ml|robot|experiment", t)
            else "study")

    resources = q("select * from resources")
    picks = [r for r in resources if r["kind"] == kind and r["capacity"] >= size]
    if not picks:
        picks = [r for r in resources if r["capacity"] >= size]

    suggestions = []
    for r in picks:
        s = next_slot(r["id"])
        if s:
            suggestions.append({
                "resource_id": r["id"],
                "name": r["name"],
                "date": s[0],
                "hour": s[1],
                "location": r["location"],
                "capacity": r["capacity"],
                "manager": r["manager"],
                "eligibility": r["eligibility"],
                "approval_required": r["approval_required"]
            })

    suggestions.sort(key=lambda v: (v["date"], v["hour"]))
    if not suggestions:
        return jsonify(reply="Nothing suitable is free in the next 3 days. Try another time, group size, or resource type.", suggestions=[])

    best = suggestions[0]
    reply = (f"Best match: {best['name']}. "
             f"Location: {best['location']}. Capacity: {best['capacity']}. "
             f"Next free: {best['date']} at {best['hour']}:00. "
             f"Managed by: {best['manager']}. "
             f"Eligible: {best['eligibility']}. "
             f"Access: {'approval required' if best['approval_required'] else 'direct booking'}.")
    if len(suggestions) > 1:
        reply += " Alternatives: " + ", ".join(f"{s['name']} at {s['hour']}:00" for s in suggestions[1:3]) + "."
    return jsonify(reply=reply, suggestions=suggestions[:3])

# ---------- Admin ----------
@app.get("/api/admin/stats")
def stats():
    B = q("select * from bookings"); days = len({b["date"] for b in B}) or 1; rows = []
    for r in q("select * from resources"):
        L = [b for b in B if b["resource_id"] == r["id"]]; nb = [b for b in L if b["status"] != "CANCELLED"]
        ns = len([b for b in L if b["status"] == "NO_SHOW"])
        rows.append(dict(name=r["name"], available=days * (DAY1 - DAY0), booked=len(nb), used=len([b for b in L if b["status"] in ("CHECKED_IN", "COMPLETED")]),
                         noshow=ns, cancelled=len(L) - len(nb), waiting=one("select count(*) c from waitlist where resource_id=?", (r["id"],))["c"],
                         noshow_rate=round(100 * ns / len(nb)) if nb else 0))
    c = lambda *s: len([b for b in B if b["status"] in s])
    pend = q("select b.id,b.date,b.hour,u.name uname,r.name rname from bookings b join users u on u.id=b.user_id join resources r on r.id=b.resource_id where b.status='CONFIRMED'")
    return jsonify(total=len(B), active=c("CONFIRMED", "CHECKED_IN"), pending_approval=c("PENDING_APPROVAL"), completed=c("COMPLETED"), cancelled=c("CANCELLED"), noshows=c("NO_SHOW"),
                   waiting=one("select count(*) c from waitlist")["c"], released=c("NO_SHOW", "CANCELLED"), resources=rows, pending=pend,
                   students=q("select u.name,(select count(*) from bookings where user_id=u.id and status='NO_SHOW') ns from users u where role='student'"))

@app.post("/api/admin/advance")
def advance(): setcfg("offset", cfg("offset") + int(J()["minutes"])); sweep(); return jsonify(now=ts())
@app.post("/api/admin/realclock")
def realclock(): setcfg("offset", 0); return jsonify(now=ts())
@app.post("/api/admin/config")
def config():
    for k, v in J().items():
        if k in ("grace", "confirm", "cancel_by", "max"): setcfg(k, v)
    return jsonify(ok=1)
@app.post("/api/admin/demo-reset")
def demo_reset():
    seed(); target = datetime.now().replace(hour=10, minute=30, second=0, microsecond=0)
    setcfg("offset", round((target - datetime.now()).total_seconds() / 60)); return jsonify(now=ts())

def loop():
    while True:
        time.sleep(15)
        try: sweep()
        except Exception as e: print("sweep error:", e)

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    print("CampuSCO running at http://localhost:5000")
    app.run(port=5000, debug=False)
