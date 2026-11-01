import os
from datetime import date, timedelta
from pathlib import Path
from flask import Flask
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
from .db import close_db, get_db, init_db


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "nexvents-local-development-key-change-before-deploying"),
        DATABASE=os.path.join(app.instance_path, "nexvents.sqlite"),
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
        GEMINI_MODEL=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        seed_demo_data()

    from .routes import bp
    app.register_blueprint(bp)

    @app.context_processor
    def inject_globals():
        from flask import session
        return {"current_user": session.get("user"), "today": date.today().isoformat()}

    return app


def seed_demo_data():
    db = get_db()
    admin = db.execute("SELECT id FROM users WHERE email = ?", ("admin@nexvents.demo",)).fetchone()
    if not admin:
        cur = db.execute(
            "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
            ("NexVents Admin", "admin@nexvents.demo", generate_password_hash("Admin@123"), "admin"),
        )
        admin_id = cur.lastrowid
    else:
        admin_id = admin["id"]

    if db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 0:
        today = date.today()
        examples = [
            ("Python for Beginners Workshop", "Technology", "A practical introduction to Python covering variables, conditions and small coding exercises. Bring your laptop if you have one.", "Computer Lab 2", (today + timedelta(days=18)).isoformat(), "10:00", 60, 0, "blue"),
            ("Inter-College CodeSprint", "Hackathon", "Form a team, choose a problem and build a working prototype during this one-day coding challenge. Shortlisted teams will present to the judges.", "Seminar Hall", (today + timedelta(days=25)).isoformat(), "09:30", 80, 0, "green"),
            ("Campus Sports Trials", "Sports", "Open trials for students interested in representing the college. Bring your college ID, sports shoes and water bottle.", "Main Sports Ground", (today + timedelta(days=34)).isoformat(), "11:00", 120, 0, "orange"),
        ]
        db.executemany(
            "INSERT INTO events(title,category,description,venue,event_date,event_time,capacity,price,cover,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [row + (admin_id,) for row in examples],
        )
    db.commit()
