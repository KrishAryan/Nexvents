import io
import os
import re
import secrets
import string
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import qrcode

from .db import get_db

bp = Blueprint("main", __name__)
COVERS = {"violet", "blue", "pink", "green", "orange"}


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            flash("Please log in to continue.", "info")
            return redirect(url_for("main.login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            flash("Admin login is required.", "info")
            return redirect(url_for("main.login"))
        if session["user"].get("role") != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


def coordinator_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = session.get("user")
        if not user or user.get("role") != "coordinator":
            flash("Coordinator login is required.", "info")
            return redirect(url_for("main.coordinator_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def make_coordinator_code():
    return "NXC-" + secrets.token_hex(4).upper()


def make_ticket_code():
    alphabet = string.ascii_uppercase + string.digits
    return "NX-" + "".join(secrets.choice(alphabet) for _ in range(10))


def event_counts(event_id):
    db = get_db()
    row = db.execute(
        "SELECT SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) confirmed, "
        "SUM(CASE WHEN status='waitlisted' THEN 1 ELSE 0 END) waitlisted "
        "FROM registrations WHERE event_id=?", (event_id,)
    ).fetchone()
    return {"confirmed": row["confirmed"] or 0, "waitlisted": row["waitlisted"] or 0}



def smart_recommendations(user_id=None, limit=4):
    """Rank upcoming events using a transparent local interest + popularity score."""
    db = get_db()
    weights = {}
    if user_id:
        history = db.execute(
            "SELECT e.category, COUNT(*) AS n FROM registrations r JOIN events e ON e.id=r.event_id "
            "WHERE r.user_id=? AND r.status IN ('confirmed','waitlisted') GROUP BY e.category",
            (user_id,),
        ).fetchall()
        weights = {row["category"].lower(): row["n"] * 4 for row in history}
    rows = db.execute(
        "SELECT e.*, (SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id AND r.status='confirmed') AS popularity "
        "FROM events e WHERE e.event_date>=? ORDER BY e.event_date ASC",
        (date.today().isoformat(),),
    ).fetchall()
    ranked = []
    for row in rows:
        if user_id:
            prior = db.execute(
                "SELECT 1 FROM registrations WHERE user_id=? AND event_id=? AND status IN ('confirmed','waitlisted')",
                (user_id, row["id"]),
            ).fetchone()
            if prior:
                continue
        score = weights.get(row["category"].lower(), 0) + min(row["popularity"], 10) * 0.25
        # With no history, slightly favour events with more engagement while retaining date order.
        ranked.append((score, row["event_date"], row["id"]))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [get_event(item[2]) for item in ranked[:limit]]


def get_event(event_id):
    event = get_db().execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not event:
        abort(404)
    data = dict(event)
    counts = event_counts(event_id)
    data.update(counts)
    data["seats_left"] = max(0, data["capacity"] - counts["confirmed"])
    stay_count = get_db().execute(
        "SELECT COUNT(*) AS n FROM registrations WHERE event_id=? AND status='confirmed' AND stay_selected=1",
        (event_id,),
    ).fetchone()["n"]
    data["stay_booked"] = stay_count
    data["stay_slots_left"] = max(0, data.get("accommodation_capacity", 0) - stay_count)
    return data


@bp.route("/")
def home():
    db = get_db()
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    query = "SELECT * FROM events WHERE event_date >= ?"
    params = [date.today().isoformat()]
    if search:
        query += " AND (title LIKE ? OR description LIKE ? OR venue LIKE ?)"
        params.extend([f"%{search}%"] * 3)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY event_date ASC, event_time ASC"
    events = db.execute(query, params).fetchall()
    cards = [get_event(e["id"]) for e in events]
    categories = [r["category"] for r in db.execute("SELECT DISTINCT category FROM events ORDER BY category")]
    return render_template("home.html", events=cards, categories=categories, search=search, selected_category=category)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user"):
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(name) < 2 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid name and email address.", "error")
        elif len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            db = get_db()
            try:
                cur = db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'student')",
                                 (name, email, generate_password_hash(password)))
                db.commit()
                session.clear()
                session["user"] = {"id": cur.lastrowid, "name": name, "email": email, "role": "student"}
                flash("Your NexVents account is ready. Welcome aboard!", "success")
                return redirect(url_for("main.dashboard"))
            except Exception as exc:
                db.rollback()
                if "UNIQUE" in str(exc).upper():
                    flash("An account with this email already exists. Please log in.", "error")
                else:
                    current_app.logger.exception("Registration failed")
                    flash("Could not create your account. Please try again.", "error")
    return render_template("auth.html", mode="register")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user"] = {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}
            flash(f"Welcome back, {user['name'].split()[0]}!", "success")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("main.dashboard"))
        flash("Incorrect email or password.", "error")
    return render_template("auth.html", mode="login")


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("main.home"))


@bp.route("/dashboard")
@login_required
def dashboard():
    user = session["user"]
    if user["role"] == "admin":
        return redirect(url_for("main.admin_dashboard"))
    if user["role"] == "coordinator":
        return redirect(url_for("main.coordinator_dashboard"))
    rows = get_db().execute(
        "SELECT r.*, e.title, e.category, e.venue, e.event_date, e.event_time, e.cover "
        "FROM registrations r JOIN events e ON e.id=r.event_id WHERE r.user_id=? "
        "AND r.status != 'cancelled' ORDER BY e.event_date ASC", (user["id"],)
    ).fetchall()
    recommendations = smart_recommendations(user["id"], limit=3)
    return render_template("dashboard.html", registrations=rows, recommendations=recommendations)


@bp.route("/events/<int:event_id>")
def event_detail(event_id):
    event = get_event(event_id)
    registered = None
    if session.get("user") and session["user"].get("role") in {"student", "admin"}:
        registered = get_db().execute(
            "SELECT * FROM registrations WHERE user_id=? AND event_id=? AND status!='cancelled'",
            (session["user"]["id"], event_id)
        ).fetchone()
    return render_template("event_detail.html", event=event, registration=registered)


@bp.route("/events/<int:event_id>/register", methods=["POST"])
@login_required
def event_register(event_id):
    user = session["user"]
    if user["role"] != "student":
        flash("Student accounts are used to register for events.", "info")
        return redirect(url_for("main.event_detail", event_id=event_id))
    event = get_event(event_id)
    if event["event_date"] < date.today().isoformat():
        flash("Registration is closed because this event has passed.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))
    db = get_db()
    existing = db.execute("SELECT * FROM registrations WHERE user_id=? AND event_id=?",
                          (user["id"], event_id)).fetchone()
    if existing and existing["status"] != "cancelled":
        flash("You already have a registration for this event.", "info")
        return redirect(url_for("main.ticket", registration_id=existing["id"]))

    counts = event_counts(event_id)
    status = "confirmed" if counts["confirmed"] < event["capacity"] else "waitlisted"
    stay_selected = 1 if request.form.get("stay_selected") in {"on", "1", "true"} else 0
    food_selected = 1 if request.form.get("food_selected") in {"on", "1", "true"} else 0
    addon_total = 0.0

    if not event.get("accommodation_enabled"):
        stay_selected = food_selected = 0
    if status == "waitlisted":
        # Do not reserve or charge for add-ons before an event seat is confirmed.
        stay_selected = food_selected = 0
        flash("The event is full. You have been waitlisted; accommodation add-ons can be selected after a seat is confirmed.", "info")
    else:
        if stay_selected:
            stay_count = db.execute(
                "SELECT COUNT(*) AS n FROM registrations WHERE event_id=? AND status='confirmed' AND stay_selected=1",
                (event_id,),
            ).fetchone()["n"]
            if stay_count >= event.get("accommodation_capacity", 0):
                stay_selected = 0
                flash("Stay accommodation is full, but you can continue with your event registration and any food add-on.", "info")
            else:
                addon_total += float(event.get("stay_price") or 0)
        if food_selected:
            addon_total += float(event.get("food_price") or 0)

    code = make_ticket_code()
    try:
        if existing:
            db.execute(
                "UPDATE registrations SET ticket_code=?,status=?,registered_at=CURRENT_TIMESTAMP,checked_in_at=NULL,"
                "stay_selected=?,food_selected=?,addon_total=? WHERE id=?",
                (code, status, stay_selected, food_selected, addon_total, existing["id"]),
            )
            registration_id = existing["id"]
        else:
            cur = db.execute(
                "INSERT INTO registrations(user_id,event_id,ticket_code,status,stay_selected,food_selected,addon_total) "
                "VALUES(?,?,?,?,?,?,?)",
                (user["id"], event_id, code, status, stay_selected, food_selected, addon_total),
            )
            registration_id = cur.lastrowid
        db.commit()
        if status == "confirmed":
            if addon_total:
                flash(f"Registration confirmed. Stay/food add-ons totalling ₹{addon_total:.2f} have been added to your booking.", "success")
            else:
                flash("Registration confirmed! Your QR ticket is ready.", "success")
        else:
            flash("This event is full. You have been added to the waitlist; no confirmed ticket is issued yet.", "info")
        return redirect(url_for("main.ticket", registration_id=registration_id))
    except Exception:
        db.rollback()
        current_app.logger.exception("Event registration failed")
        flash("Registration could not be completed. Please retry.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))


@bp.route("/tickets/<int:registration_id>")
@login_required
def ticket(registration_id):
    row = get_db().execute(
        "SELECT r.*, u.name, u.email, e.title, e.category, e.venue, e.event_date, e.event_time, "
        "e.accommodation_enabled,e.stay_price,e.food_price,e.accommodation_capacity "
        "FROM registrations r JOIN users u ON u.id=r.user_id JOIN events e ON e.id=r.event_id WHERE r.id=?",
        (registration_id,)
    ).fetchone()
    if not row:
        abort(404)
    role = session["user"].get("role")
    if role not in {"admin", "student"} or (role == "student" and row["user_id"] != session["user"]["id"]):
        abort(403)
    ticket_data = dict(row)
    if ticket_data.get("accommodation_enabled"):
        ticket_data["stay_slots_left"] = max(0, ticket_data.get("accommodation_capacity", 0) - get_db().execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE event_id=? AND status='confirmed' AND stay_selected=1 AND id!=?",
            (ticket_data["event_id"], ticket_data["id"]),
        ).fetchone()["n"])
    else:
        ticket_data["stay_slots_left"] = 0
    return render_template("ticket.html", ticket=ticket_data)


@bp.route("/tickets/<int:registration_id>/addons", methods=["POST"])
@login_required
def update_registration_addons(registration_id):
    if session["user"].get("role") != "student":
        abort(403)
    db = get_db()
    row = db.execute(
        "SELECT r.*,e.accommodation_enabled,e.stay_price,e.food_price,e.accommodation_capacity "
        "FROM registrations r JOIN events e ON e.id=r.event_id WHERE r.id=? AND r.user_id=?",
        (registration_id, session["user"]["id"]),
    ).fetchone()
    if not row:
        abort(404)
    if row["status"] != "confirmed" or not row["accommodation_enabled"]:
        flash("Add-ons can only be updated for a confirmed registration when the organiser has enabled them.", "error")
        return redirect(url_for("main.ticket", registration_id=registration_id))

    stay_selected = 1 if request.form.get("stay_selected") in {"on", "1", "true"} else 0
    food_selected = 1 if request.form.get("food_selected") in {"on", "1", "true"} else 0
    if stay_selected:
        stay_count = db.execute(
            "SELECT COUNT(*) AS n FROM registrations WHERE event_id=? AND status='confirmed' AND stay_selected=1 AND id!=?",
            (row["event_id"], registration_id),
        ).fetchone()["n"]
        if stay_count >= row["accommodation_capacity"]:
            flash("Stay accommodation is full. Your previous stay selection has been kept.", "error")
            return redirect(url_for("main.ticket", registration_id=registration_id))
    total = (float(row["stay_price"] or 0) if stay_selected else 0) + (float(row["food_price"] or 0) if food_selected else 0)
    db.execute(
        "UPDATE registrations SET stay_selected=?,food_selected=?,addon_total=? WHERE id=? AND user_id=?",
        (stay_selected, food_selected, total, registration_id, session["user"]["id"]),
    )
    db.commit()
    flash("Your accommodation and food selections have been updated.", "success")
    return redirect(url_for("main.ticket", registration_id=registration_id))


@bp.route("/tickets/<ticket_code>/qr.png")
@login_required
def ticket_qr(ticket_code):
    row = get_db().execute("SELECT user_id FROM registrations WHERE ticket_code=?", (ticket_code,)).fetchone()
    if not row:
        abort(404)
    role = session["user"].get("role")
    if role not in {"admin", "student"} or (role == "student" and row["user_id"] != session["user"]["id"]):
        abort(403)
    registration = get_db().execute("SELECT status FROM registrations WHERE ticket_code=?", (ticket_code,)).fetchone()
    if registration["status"] != "confirmed":
        abort(403)
    qr = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=9, border=3)
    qr.add_data(ticket_code)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#17112f", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", download_name=f"{ticket_code}.png")


@bp.route("/tickets/<int:registration_id>/cancel", methods=["POST"])
@login_required
def cancel_registration(registration_id):
    db = get_db()
    row = db.execute("SELECT * FROM registrations WHERE id=?", (registration_id,)).fetchone()
    role = session["user"].get("role")
    if not row or role not in {"student", "admin"} or (role == "student" and row["user_id"] != session["user"]["id"]):
        abort(404)
    if row["status"] == "confirmed" and row["checked_in_at"]:
        flash("A checked-in ticket cannot be cancelled from the portal.", "error")
        return redirect(url_for("main.dashboard"))
    db.execute("UPDATE registrations SET status='cancelled' WHERE id=?", (registration_id,))
    if row["status"] == "confirmed":
        waiting = db.execute("SELECT id FROM registrations WHERE event_id=? AND status='waitlisted' ORDER BY registered_at ASC LIMIT 1",
                              (row["event_id"],)).fetchone()
        if waiting:
            db.execute("UPDATE registrations SET status='confirmed' WHERE id=?", (waiting["id"],))
    db.commit()
    flash("Registration cancelled. The next waitlisted participant is promoted when a seat is freed.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {
        "events": db.execute("SELECT COUNT(*) n FROM events").fetchone()["n"],
        "students": db.execute("SELECT COUNT(*) n FROM users WHERE role='student'").fetchone()["n"],
        "confirmed": db.execute("SELECT COUNT(*) n FROM registrations WHERE status='confirmed'").fetchone()["n"],
        "checked_in": db.execute("SELECT COUNT(*) n FROM registrations WHERE checked_in_at IS NOT NULL").fetchone()["n"],
    }
    events = [get_event(r["id"]) for r in db.execute("SELECT id FROM events ORDER BY event_date DESC")]
    recent = db.execute("SELECT r.id,r.ticket_code,r.status,r.registered_at,u.name,u.email,e.title "
                        "FROM registrations r JOIN users u ON u.id=r.user_id JOIN events e ON e.id=r.event_id "
                        "ORDER BY r.registered_at DESC LIMIT 8").fetchall()
    return render_template("admin.html", stats=stats, events=events, recent=recent)


@bp.route("/admin/events/new", methods=["GET", "POST"])
@admin_required
def event_new():
    return event_form(None)


@bp.route("/admin/events/<int:event_id>/edit", methods=["GET", "POST"])
@admin_required
def event_edit(event_id):
    return event_form(event_id)


def event_form(event_id):
    db = get_db()
    existing = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone() if event_id else None
    if event_id and not existing:
        abort(404)
    if request.method == "POST":
        fields = {key: request.form.get(key, "").strip() for key in
                  ["title", "category", "description", "venue", "event_date", "event_time", "capacity", "price", "cover",
                   "stay_price", "food_price", "accommodation_capacity", "medical_location", "medical_contact",
                   "security_contact", "emergency_contact"]}
        accommodation_enabled = 1 if request.form.get("accommodation_enabled") == "on" else 0
        try:
            capacity = int(fields["capacity"])
            price = float(fields["price"] or 0)
            stay_price = float(fields["stay_price"] or 0)
            food_price = float(fields["food_price"] or 0)
            accommodation_capacity = int(fields["accommodation_capacity"] or 0)
            parsed_date = date.fromisoformat(fields["event_date"])
            datetime.strptime(fields["event_time"], "%H:%M")
            if parsed_date < date.today():
                raise ValueError("Event date cannot be in the past.")
            if not all(fields[k] for k in ["title", "category", "description", "venue", "event_time"]):
                raise ValueError("Please complete all required fields.")
            if capacity < 1 or min(price, stay_price, food_price) < 0:
                raise ValueError("Capacity must be positive and prices cannot be negative.")
            if accommodation_enabled and accommodation_capacity < 1:
                raise ValueError("Set at least one available stay slot when accommodation is enabled.")
            if accommodation_capacity < 0:
                raise ValueError("Accommodation capacity cannot be negative.")
            if fields["cover"] not in COVERS:
                fields["cover"] = "violet"
            values = (fields["title"], fields["category"], fields["description"], fields["venue"],
                      parsed_date.isoformat(), fields["event_time"], capacity, price, fields["cover"],
                      accommodation_enabled, stay_price, food_price, accommodation_capacity,
                      fields["medical_location"], fields["medical_contact"], fields["security_contact"], fields["emergency_contact"])
            if event_id:
                db.execute("UPDATE events SET title=?,category=?,description=?,venue=?,event_date=?,event_time=?,capacity=?,price=?,cover=?,accommodation_enabled=?,stay_price=?,food_price=?,accommodation_capacity=?,medical_location=?,medical_contact=?,security_contact=?,emergency_contact=? WHERE id=?",
                           values + (event_id,))
                flash("Event updated successfully.", "success")
            else:
                db.execute("INSERT INTO events(title,category,description,venue,event_date,event_time,capacity,price,cover,accommodation_enabled,stay_price,food_price,accommodation_capacity,medical_location,medical_contact,security_contact,emergency_contact,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           values + (session["user"]["id"],))
                flash("Your event is live on NexVents.", "success")
            db.commit()
            return redirect(url_for("main.admin_dashboard"))
        except ValueError as exc:
            flash(str(exc) or "Please enter valid event details.", "error")
        except Exception:
            db.rollback()
            flash("Could not save the event. Check the details and try again.", "error")
    return render_template("event_form.html", event=existing)


@bp.route("/admin/events/<int:event_id>/delete", methods=["POST"])
@admin_required
def event_delete(event_id):
    db = get_db()
    db.execute("DELETE FROM events WHERE id=?", (event_id,))
    db.commit()
    flash("Event and its associated registrations have been deleted.", "success")
    return redirect(url_for("main.admin_dashboard"))


@bp.route("/coordinator/login", methods=["GET", "POST"])
def coordinator_login():
    if session.get("user"):
        role = session["user"].get("role")
        return redirect(url_for("main.coordinator_dashboard" if role == "coordinator" else "main.dashboard"))
    if request.method == "POST":
        coordinator_code = request.form.get("coordinator_code", "").strip().upper()
        password = request.form.get("password", "")
        coordinator = get_db().execute(
            "SELECT * FROM coordinators WHERE coordinator_code=? AND is_active=1", (coordinator_code,)
        ).fetchone()
        if coordinator and check_password_hash(coordinator["password_hash"], password):
            session.clear()
            session["user"] = {
                "id": coordinator["id"], "name": coordinator["name"], "email": coordinator["email"],
                "role": "coordinator", "coordinator_code": coordinator["coordinator_code"],
            }
            flash(f"Welcome back, {coordinator['name'].split()[0]}.", "success")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("main.coordinator_dashboard"))
        flash("Coordinator ID or password is incorrect.", "error")
    return render_template("coordinator_login.html")


@bp.route("/coordinator")
@coordinator_required
def coordinator_dashboard():
    db = get_db()
    coordinator_id = session["user"]["id"]
    events = db.execute(
        "SELECT e.*, ec.assigned_at FROM events e JOIN event_coordinators ec ON ec.event_id=e.id "
        "WHERE ec.coordinator_id=? ORDER BY e.event_date, e.event_time", (coordinator_id,)
    ).fetchall()
    event_panels = []
    for event_row in events:
        event = dict(event_row)
        event_id = event["id"]
        event["team"] = db.execute(
            "SELECT c.id,c.name,c.coordinator_code FROM coordinators c "
            "JOIN event_coordinators ec ON ec.coordinator_id=c.id "
            "WHERE ec.event_id=? ORDER BY c.name", (event_id,)
        ).fetchall()
        event["messages"] = db.execute(
            "SELECT m.message,m.created_at,c.name,c.coordinator_code FROM coordinator_messages m "
            "JOIN coordinators c ON c.id=m.coordinator_id WHERE m.event_id=? "
            "ORDER BY m.created_at DESC,m.id DESC LIMIT 25", (event_id,)
        ).fetchall()
        event_panels.append(event)
    return render_template("coordinator_dashboard.html", events=event_panels)


@bp.route("/coordinator/events/<int:event_id>/messages", methods=["POST"])
@coordinator_required
def coordinator_message(event_id):
    db = get_db()
    assigned = db.execute(
        "SELECT 1 FROM event_coordinators WHERE event_id=? AND coordinator_id=?",
        (event_id, session["user"]["id"]),
    ).fetchone()
    if not assigned:
        abort(403)
    message = request.form.get("message", "").strip()
    if not message or len(message) > 1000:
        flash("Messages must contain between 1 and 1,000 characters.", "error")
    else:
        db.execute(
            "INSERT INTO coordinator_messages(event_id,coordinator_id,message) VALUES(?,?,?)",
            (event_id, session["user"]["id"], message),
        )
        db.commit()
        flash("Message posted to the event team.", "success")
    return redirect(url_for("main.coordinator_dashboard") + f"#event-{event_id}")


@bp.route("/admin/coordinators", methods=["GET", "POST"])
@admin_required
def coordinator_management():
    db = get_db()
    events = db.execute("SELECT id,title,event_date FROM events ORDER BY event_date").fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        selected_ids = request.form.getlist("event_ids")
        if len(name) < 2 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a coordinator name and valid email address.", "error")
        elif len(password) < 8:
            flash("Temporary password must contain at least 8 characters.", "error")
        else:
            code = make_coordinator_code()
            while db.execute("SELECT 1 FROM coordinators WHERE coordinator_code=?", (code,)).fetchone():
                code = make_coordinator_code()
            try:
                cur = db.execute(
                    "INSERT INTO coordinators(coordinator_code,name,email,password_hash) VALUES(?,?,?,?)",
                    (code, name, email, generate_password_hash(password)),
                )
                coordinator_id = cur.lastrowid
                valid_event_ids = []
                for raw_id in selected_ids:
                    try:
                        valid_event_ids.append(int(raw_id))
                    except (TypeError, ValueError):
                        continue
                for event_id in set(valid_event_ids):
                    exists = db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone()
                    if exists:
                        db.execute("INSERT OR IGNORE INTO event_coordinators(event_id,coordinator_id) VALUES(?,?)", (event_id, coordinator_id))
                db.commit()
                flash(f"Coordinator created. Share this login ID with {name}: {code}", "success")
                return redirect(url_for("main.coordinator_management"))
            except Exception as exc:
                db.rollback()
                if "UNIQUE" in str(exc).upper():
                    flash("A coordinator with this email already exists.", "error")
                else:
                    current_app.logger.exception("Coordinator creation failed")
                    flash("Could not create coordinator. Please try again.", "error")
    coordinators = db.execute(
        "SELECT c.*, GROUP_CONCAT(e.title, ' · ') AS event_titles FROM coordinators c "
        "LEFT JOIN event_coordinators ec ON ec.coordinator_id=c.id "
        "LEFT JOIN events e ON e.id=ec.event_id GROUP BY c.id ORDER BY c.created_at DESC"
    ).fetchall()
    assignments = {}
    for row in db.execute("SELECT coordinator_id,event_id FROM event_coordinators"):
        assignments.setdefault(row["coordinator_id"], set()).add(row["event_id"])
    return render_template("coordinator_manage.html", coordinators=coordinators, events=events, assignments=assignments)


@bp.route("/admin/coordinators/<int:coordinator_id>/assign", methods=["POST"])
@admin_required
def coordinator_assign(coordinator_id):
    db = get_db()
    exists = db.execute("SELECT id FROM coordinators WHERE id=?", (coordinator_id,)).fetchone()
    if not exists:
        abort(404)
    selected_ids = set()
    for raw_id in request.form.getlist("event_ids"):
        try:
            selected_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    valid_ids = {row["id"] for row in db.execute("SELECT id FROM events")}
    db.execute("DELETE FROM event_coordinators WHERE coordinator_id=?", (coordinator_id,))
    for event_id in selected_ids & valid_ids:
        db.execute("INSERT INTO event_coordinators(event_id,coordinator_id) VALUES(?,?)", (event_id, coordinator_id))
    db.commit()
    flash("Coordinator event assignments updated.", "success")
    return redirect(url_for("main.coordinator_management"))


@bp.route("/admin/accommodation")
@admin_required
def accommodation_bookings():
    rows = get_db().execute(
        "SELECT r.id,r.stay_selected,r.food_selected,r.addon_total,r.status,r.ticket_code,"
        "u.name,u.email,e.title,e.event_date FROM registrations r "
        "JOIN users u ON u.id=r.user_id JOIN events e ON e.id=r.event_id "
        "WHERE r.status!='cancelled' AND (r.stay_selected=1 OR r.food_selected=1) "
        "ORDER BY e.event_date,r.registered_at DESC"
    ).fetchall()
    totals = {
        "bookings": len(rows),
        "stay": sum(1 for row in rows if row["stay_selected"]),
        "food": sum(1 for row in rows if row["food_selected"]),
        "amount": sum(float(row["addon_total"] or 0) for row in rows),
    }
    return render_template("admin_accommodation.html", bookings=rows, totals=totals)


def verify_ticket_code(code):
    """Validate one ticket and record attendance exactly once."""
    code = (code or "").strip().upper()
    code_match = re.search(r"NX-[A-Z0-9]{10}", code)
    if code_match:
        code = code_match.group(0)

    if not re.fullmatch(r"NX-[A-Z0-9]{10}", code):
        return {"ok": False, "message": "That QR does not contain a valid NexVents ticket code."}

    db = get_db()
    row = db.execute(
        "SELECT r.*,u.name,u.email,e.title,e.event_date,e.event_time,e.venue "
        "FROM registrations r JOIN users u ON u.id=r.user_id "
        "JOIN events e ON e.id=r.event_id WHERE r.ticket_code=?", (code,)
    ).fetchone()

    if not row:
        return {"ok": False, "message": "Ticket code not found."}
    if row["status"] != "confirmed":
        return {"ok": False, "message": f"Ticket is {row['status']}. Only confirmed tickets can be checked in.", "row": dict(row)}
    if row["checked_in_at"]:
        return {"ok": False, "message": f"Already checked in at {row['checked_in_at']}.", "row": dict(row)}

    # Conditional update prevents two scanners from recording the same entry twice.
    updated = db.execute(
        "UPDATE registrations SET checked_in_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='confirmed' AND checked_in_at IS NULL", (row["id"],)
    )
    db.commit()
    fresh = db.execute(
        "SELECT r.*,u.name,u.email,e.title,e.event_date,e.event_time,e.venue "
        "FROM registrations r JOIN users u ON u.id=r.user_id "
        "JOIN events e ON e.id=r.event_id WHERE r.id=?", (row["id"],)
    ).fetchone()
    if updated.rowcount != 1:
        return {"ok": False, "message": "This ticket has already been checked in.", "row": dict(fresh)}
    return {"ok": True, "message": "Check-in successful. Attendance recorded.", "row": dict(fresh)}


@bp.route("/admin/check-in", methods=["GET", "POST"])
@admin_required
def checkin():
    result = None
    if request.method == "POST":
        result = verify_ticket_code(request.form.get("ticket_code", ""))
    return render_template("checkin.html", result=result)


@bp.route("/admin/check-in/scan", methods=["POST"])
@admin_required
def scan_ticket_qr():
    """Decode a camera frame or uploaded QR image with the local Python ZXing package."""
    upload = request.files.get("frame") or request.files.get("qr_image")
    if not upload:
        return {"found": False, "error": "No image was received by the scanner."}, 400
    if request.content_length and request.content_length > 5 * 1024 * 1024:
        return {"found": False, "error": "Image is too large. Use an image under 5 MB."}, 413

    try:
        from PIL import Image
        import zxingcpp
        image = Image.open(upload.stream).convert("RGB")
        image.thumbnail((1600, 1600))
        barcodes = zxingcpp.read_barcodes(image)
    except ImportError:
        current_app.logger.exception("QR decoder dependency is missing")
        return {"found": False, "error": "QR decoder is not installed. Run: python -m pip install -r requirements.txt"}, 503
    except Exception as exc:
        # A camera frame can briefly be empty while the video starts; treat it as no decode.
        current_app.logger.debug("QR frame could not be decoded: %s", exc)
        return {"found": False, "message": "No QR found yet. Keep the ticket inside the frame."}

    if not barcodes:
        return {"found": False, "message": "No QR found yet. Keep the ticket inside the frame."}

    decoded_text = next((item.text for item in barcodes if getattr(item, "text", "")), "")
    if not decoded_text:
        return {"found": True, "ok": False, "message": "The QR code could not be read as text."}
    result = verify_ticket_code(decoded_text)
    row = result.get("row")
    return {
        "found": True,
        "ok": result["ok"],
        "message": result["message"],
        "attendee": row.get("name") if row else None,
        "event": row.get("title") if row else None,
        "ticket_code": row.get("ticket_code") if row else None,
    }


@bp.route("/ai-assistant", methods=["GET", "POST"])
def ai_assistant():
    answer = None
    question = ""
    if request.method == "POST":
        question = request.form.get("message", "").strip()
        if question:
            answer = ask_ai(question)
    return render_template("assistant.html", answer=answer, question=question)


def ask_ai(question):
    """Use Gemini when configured; otherwise provide a local, no-key smart helper."""
    api_key = current_app.config.get("GEMINI_API_KEY")
    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = ("You are NexVents AI, a concise and friendly college event assistant. "
                      "Help with event planning, participation, schedules, team ideas and event discovery. "
                      "Do not claim to perform registrations or change event records. User asks: " + question)
            response = client.models.generate_content(model=current_app.config["GEMINI_MODEL"], contents=prompt)
            if response and response.text:
                return response.text.strip()
        except Exception:
            current_app.logger.exception("Gemini request failed; using local assistant")

    q = question.lower()
    events = get_db().execute("SELECT id,title,category,event_date,venue FROM events WHERE event_date>=? ORDER BY event_date LIMIT 8",
                              (date.today().isoformat(),)).fetchall()
    if any(word in q for word in ["recommend", "suggest", "which event", "events", "upcoming"]):
        user = session.get("user")
        picks = smart_recommendations(user["id"] if user and user.get("role") == "student" else None, limit=5)
        if picks:
            lines = [f"• {e['title']} — {e['category']}, {e['event_date']} at {e['venue']} (open event #{e['id']})" for e in picks]
            intro = "Based on your NexVents activity, these are my picks:" if user and user.get("role") == "student" else "Here are the upcoming events on NexVents:"
            return intro + "\n" + "\n".join(lines) + "\n\nOpen an event card to view details and register."
        return "There are no upcoming events listed right now. Check again after the organiser publishes one."
    if any(word in q for word in ["register", "ticket", "qr", "book"]):
        return "Open an event, select Register Now, and sign in with your student account. Confirmed registrations receive a unique QR ticket in My Dashboard. If the event is full, you are placed on the waitlist."
    if any(word in q for word in ["organise", "create event", "host", "plan"]):
        return "A good event plan includes a clear goal, audience, date, venue, capacity, budget, organiser roles, registration flow and a check-in desk. On NexVents, an admin can publish an event from the Admin Dashboard."
    if any(word in q for word in ["check in", "attendance", "scan"]):
        return "The organiser opens Admin → QR Check-in, scans the attendee's QR using the camera, or enters the ticket code manually. A confirmed ticket can be checked in only once."
    if any(word in q for word in ["accommodation", "stay", "food", "hostel"]):
        return "Open the event details page. When the organiser has enabled add-ons, you can select Stay, Food, both or neither during registration. NexVents shows and saves the calculated add-on total with your booking. Online payment is not enabled in this demo."
    if any(word in q for word in ["medical", "emergency", "safety", "first aid", "security"]):
        return "Open the event details page and look for Medical & Safety Support. The organiser's event-specific first-aid location and contact numbers are shown there. For immediate danger, contact local emergency services."
    if "coordinator" in q:
        return "Coordinators sign in through Coordinator login using the unique Coordinator ID and password provided by the admin. Assigned events and the event team's shared update board are available in the coordinator dashboard."
    return "I’m the NexVents Assistant. I can help you find listed events, understand registration and QR tickets, or plan a college event. Add your Gemini API key in the .env file to enable live AI-generated replies."


@bp.app_errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You don't have permission to view this page."), 403


@bp.app_errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="We couldn't find that page."), 404
