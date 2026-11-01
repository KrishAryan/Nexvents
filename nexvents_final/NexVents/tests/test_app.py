from datetime import date, timedelta
from io import BytesIO
import sqlite3
import pytest
from app import create_app
from app.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE": str(tmp_path / "test.sqlite"),
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def register_student(client, email="student@example.com"):
    return client.post("/register", data={
        "name": "Test Student",
        "email": email,
        "password": "Password123",
        "confirm_password": "Password123",
    }, follow_redirects=True)


def login_admin(client):
    return client.post("/login", data={
        "email": "admin@nexvents.demo",
        "password": "Admin@123",
    }, follow_redirects=True)


def test_home_register_and_student_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"NexVents" in response.data
    response = register_student(client)
    assert response.status_code == 200
    assert b"My events" in response.data


def test_event_registration_qr_and_single_checkin(app, client):
    register_student(client)
    with app.app_context():
        event_id = get_db().execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()["id"]
    response = client.post(f"/events/{event_id}/register", follow_redirects=True)
    assert response.status_code == 200
    assert b"UNIQUE TICKET CODE" in response.data
    client.post(f"/events/{event_id}/register")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) n FROM registrations WHERE event_id=?", (event_id,)).fetchone()["n"] == 1
    with app.app_context():
        row = get_db().execute("SELECT id,ticket_code FROM registrations WHERE event_id=?", (event_id,)).fetchone()
        registration_id, code = row["id"], row["ticket_code"]
    qr_response = client.get(f"/tickets/{code}/qr.png")
    assert qr_response.mimetype == "image/png"
    assert qr_response.data.startswith(b"\x89PNG\r\n\x1a\n")
    client.get("/logout")
    login_admin(client)

    # Full QR round trip: ticket PNG -> QR decoder endpoint -> attendance record.
    scanned = client.post(
        "/admin/check-in/scan",
        data={"qr_image": (BytesIO(qr_response.data), "ticket.png")},
        content_type="multipart/form-data",
    )
    assert scanned.status_code == 200
    assert scanned.json["found"] is True
    assert scanned.json["ok"] is True
    assert scanned.json["ticket_code"] == code

    first = client.post("/admin/check-in", data={"ticket_code": code}, follow_redirects=True)
    assert b"Already checked in" in first.data


def test_waitlist_promotion_and_duplicate_prevention(app, client):
    with app.app_context():
        db = get_db()
        admin_id = db.execute("SELECT id FROM users WHERE email='admin@nexvents.demo'").fetchone()["id"]
        event_id = db.execute(
            "INSERT INTO events(title,category,description,venue,event_date,event_time,capacity,price,cover,created_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("Tiny Event", "Test", "A test event", "Lab", (date.today()+timedelta(days=7)).isoformat(), "10:00", 1, 0, "violet", admin_id),
        ).lastrowid
        db.commit()
    register_student(client, "first@example.com")
    client.post(f"/events/{event_id}/register")
    client.get("/logout")
    register_student(client, "second@example.com")
    client.post(f"/events/{event_id}/register")
    with app.app_context():
        rows = get_db().execute("SELECT id,user_id,status FROM registrations WHERE event_id=? ORDER BY id", (event_id,)).fetchall()
        assert [r["status"] for r in rows] == ["confirmed", "waitlisted"]
        first_id = rows[0]["id"]
    client.get("/logout")
    client.post("/login", data={"email": "first@example.com", "password": "Password123"})
    client.post(f"/tickets/{first_id}/cancel")
    with app.app_context():
        rows = get_db().execute("SELECT status FROM registrations WHERE event_id=? ORDER BY id", (event_id,)).fetchall()
        assert [r["status"] for r in rows] == ["cancelled", "confirmed"]



def test_accommodation_booking_is_saved_and_support_details_are_public(app, client):
    with app.app_context():
        db = get_db()
        event_id = db.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()["id"]
        db.execute(
            "UPDATE events SET accommodation_enabled=1,stay_price=450,food_price=180,"
            "accommodation_capacity=10,medical_location=?,medical_contact=?,security_contact=?,emergency_contact=? WHERE id=?",
            ("Medical Room, Block A", "9876543210", "9876500000", "112", event_id),
        )
        db.commit()
    register_student(client, "addons@example.com")
    event_page = client.get(f"/events/{event_id}")
    assert b"Stay and food" in event_page.data
    assert b"Medical & safety support" in event_page.data
    response = client.post(f"/events/{event_id}/register", data={"stay_selected": "1", "food_selected": "1"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"SELECTED ADD-ONS" in response.data
    with app.app_context():
        row = get_db().execute(
            "SELECT stay_selected,food_selected,addon_total FROM registrations WHERE user_id=? AND event_id=?",
            (get_db().execute("SELECT id FROM users WHERE email='addons@example.com'").fetchone()["id"], event_id),
        ).fetchone()
        assert row["stay_selected"] == 1
        assert row["food_selected"] == 1
        assert row["addon_total"] == 630
    # A confirmed participant can change add-ons later as well (useful after waitlist promotion).
    with app.app_context():
        registration_id = get_db().execute(
            "SELECT id FROM registrations WHERE ticket_code=(SELECT ticket_code FROM registrations WHERE user_id=(SELECT id FROM users WHERE email='addons@example.com') AND event_id=?)",
            (event_id,),
        ).fetchone()["id"]
    updated = client.post(f"/tickets/{registration_id}/addons", data={"food_selected": "1"}, follow_redirects=True)
    assert updated.status_code == 200
    with app.app_context():
        row = get_db().execute("SELECT stay_selected,food_selected,addon_total FROM registrations WHERE id=?", (registration_id,)).fetchone()
        assert row["stay_selected"] == 0
        assert row["food_selected"] == 1
        assert row["addon_total"] == 180


def test_admin_can_create_and_assign_coordinator_with_team_messages(app, client):
    login_admin(client)
    with app.app_context():
        event_id = get_db().execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()["id"]
    response = client.post("/admin/coordinators", data={
        "name": "Event Coordinator",
        "email": "coord@example.com",
        "password": "TempPass123",
        "event_ids": str(event_id),
    }, follow_redirects=True)
    assert response.status_code == 200
    import re
    match = re.search(rb"NXC-[A-F0-9]{8}", response.data)
    assert match, response.data.decode("utf-8", errors="replace")
    code = match.group(0).decode()

    client.get("/logout")
    response = client.post("/coordinator/login", data={
        "coordinator_code": code,
        "password": "TempPass123",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Event Coordinator" in response.data
    assert b"TEAM UPDATES" in response.data

    response = client.post(f"/coordinator/events/{event_id}/messages", data={"message": "Meet at the registration desk at 9 AM."}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Meet at the registration desk at 9 AM." in response.data
    with app.app_context():
        message = get_db().execute("SELECT message FROM coordinator_messages WHERE event_id=?", (event_id,)).fetchone()
        assert message["message"] == "Meet at the registration desk at 9 AM."


def test_coordinator_cannot_post_to_unassigned_event(app, client):
    login_admin(client)
    with app.app_context():
        event_id = get_db().execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()["id"]
    client.post("/admin/coordinators", data={
        "name": "Unassigned Coordinator",
        "email": "unassigned@example.com",
        "password": "TempPass123",
    }, follow_redirects=True)
    with app.app_context():
        code = get_db().execute("SELECT coordinator_code FROM coordinators WHERE email='unassigned@example.com'").fetchone()["coordinator_code"]
    client.get("/logout")
    client.post("/coordinator/login", data={"coordinator_code": code, "password": "TempPass123"})
    response = client.post(f"/coordinator/events/{event_id}/messages", data={"message": "Not assigned"})
    assert response.status_code == 403


def test_event_form_saves_accommodation_and_safety_contacts(client):
    login_admin(client)
    response = client.get("/admin/events/new")
    assert response.status_code == 200
    response = client.post("/admin/events/new", data={
        "title": "Campus Tech Day", "category": "Technology", "description": "A practical campus tech event.",
        "venue": "Main Auditorium", "event_date": (date.today()+timedelta(days=12)).isoformat(),
        "event_time": "10:00", "capacity": "90", "price": "100", "cover": "blue",
        "accommodation_enabled": "on", "stay_price": "500", "food_price": "200", "accommodation_capacity": "20",
        "medical_location": "First Aid Room", "medical_contact": "9876543210",
        "security_contact": "9876500000", "emergency_contact": "112",
    }, follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        event = get_db().execute("SELECT * FROM events WHERE title='Campus Tech Day'").fetchone()
        assert event["accommodation_enabled"] == 1
        assert event["stay_price"] == 500
        assert event["food_price"] == 200
        assert event["medical_location"] == "First Aid Room"
        assert event["emergency_contact"] == "112"



def test_existing_pre_update_database_is_migrated_without_losing_records(tmp_path):
    database = tmp_path / "older-nexvents.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT NOT NULL,
            description TEXT NOT NULL, venue TEXT NOT NULL, event_date TEXT NOT NULL,
            event_time TEXT NOT NULL, capacity INTEGER NOT NULL, price REAL NOT NULL DEFAULT 0,
            cover TEXT NOT NULL DEFAULT 'violet', created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, event_id INTEGER NOT NULL,
            ticket_code TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'confirmed',
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, checked_in_at TEXT,
            UNIQUE(user_id,event_id)
        );
        INSERT INTO users(name,email,password_hash,role) VALUES('Existing Student','kept@example.com','hash','student');
        INSERT INTO events(title,category,description,venue,event_date,event_time,capacity,price,cover)
        VALUES('Existing Event','Campus','Keep this record','Main Hall','2099-01-01','10:00',50,0,'blue');
        INSERT INTO registrations(user_id,event_id,ticket_code,status)
        VALUES(1,1,'NX-KEPTTICKET','confirmed');
    """)
    connection.commit()
    connection.close()

    migrated_app = create_app({"TESTING": True, "SECRET_KEY": "migration-test", "DATABASE": str(database)})
    with migrated_app.app_context():
        db = get_db()
        assert db.execute("SELECT name FROM users WHERE email='kept@example.com'").fetchone()["name"] == "Existing Student"
        assert db.execute("SELECT title FROM events WHERE id=1").fetchone()["title"] == "Existing Event"
        assert db.execute("SELECT ticket_code FROM registrations WHERE id=1").fetchone()["ticket_code"] == "NX-KEPTTICKET"
        event_columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
        registration_columns = {row["name"] for row in db.execute("PRAGMA table_info(registrations)")}
        assert {"stay_price", "food_price", "medical_contact", "emergency_contact"}.issubset(event_columns)
        assert {"stay_selected", "food_selected", "addon_total"}.issubset(registration_columns)
        assert db.execute("SELECT COUNT(*) n FROM coordinators").fetchone()["n"] == 0
