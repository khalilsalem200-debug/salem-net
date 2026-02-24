import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "salemnet.db")

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_SECRET_KEY"  # غيّرها

# =========================
# Helpers
# =========================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def add_one_month(date_str: str) -> str:
    # اشتراك شهري: نضيف 30 يوم (بسيط وعملي)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return (dt + timedelta(days=30)).strftime("%Y-%m-%d")

def is_expired(expiry_str: str) -> bool:
    exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return exp < datetime.now().date()

def days_left(expiry_str: str) -> int:
    exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return (exp - datetime.now().date()).days

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

# =========================
# DB Init
# =========================
def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        speed TEXT NOT NULL,
        price REAL NOT NULL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        address TEXT,
        service_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,
        expiry_date TEXT NOT NULL,
        FOREIGN KEY(service_id) REFERENCES services(id)
    )
    """)

    # Default admin user (لو مش موجود)
    c.execute("SELECT COUNT(*) AS cnt FROM users")
    if c.fetchone()["cnt"] == 0:
        # بيانات دخول افتراضية: admin / admin123
        c.execute("INSERT INTO users (username, password) VALUES (?,?)", ("admin", "admin123"))

    # Insert default 6 services if empty
    c.execute("SELECT COUNT(*) AS cnt FROM services")
    if c.fetchone()["cnt"] == 0:
        services = [
            ("Home 8", "8MB", 20),
            ("Home 10", "10MB", 25),
            ("Home Pro", "12MB", 30),
            ("Business 20", "14MB", 35),
            ("Business 30", "16MB", 40),
            ("VIP 50", "18MB", 50),
        ]
        c.executemany("INSERT INTO services (name, speed, price) VALUES (?,?,?)", services)

    conn.commit()
    conn.close()

init_db()

# =========================
# Auth
# =========================
@app.route("/", methods=["GET"])
def home():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()

        conn = db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = c.fetchone()
        conn.close()

        if user:
            session["user"] = username
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "danger")
        return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# Dashboard
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    c = conn.cursor()

    c.execute("""
    SELECT s.*, sv.name AS service_name, sv.speed, sv.price
    FROM subscribers s
    JOIN services sv ON sv.id = s.service_id
    ORDER BY s.id DESC
    """)
    subs = c.fetchall()

    total = len(subs)
    active = 0
    expired = 0
    expiring_soon = 0
    expected_income = 0.0

    for r in subs:
        left = days_left(r["expiry_date"])
        if left < 0:
            expired += 1
        else:
            active += 1
            expected_income += float(r["price"])
            if left <= 3:
                expiring_soon += 1

    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        active=active,
        expired=expired,
        expiring_soon=expiring_soon,
        expected_income=expected_income,
        subs=subs
    )

# =========================
# Subscribers CRUD
# =========================
@app.route("/subscribers")
@login_required
def subscribers():
    q = request.args.get("q", "").strip().lower()
    service_filter = request.args.get("service_id", "").strip()

    conn = db()
    c = conn.cursor()

    base_sql = """
    SELECT s.*, sv.name AS service_name, sv.speed, sv.price
    FROM subscribers s
    JOIN services sv ON sv.id = s.service_id
    """
    where = []
    params = []

    if q:
        where.append("(LOWER(s.name) LIKE ? OR LOWER(s.phone) LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if service_filter:
        where.append("s.service_id = ?")
        params.append(service_filter)

    if where:
        base_sql += " WHERE " + " AND ".join(where)

    base_sql += " ORDER BY s.id DESC"

    c.execute(base_sql, params)
    subs = c.fetchall()

    c.execute("SELECT * FROM services ORDER BY id")
    services = c.fetchall()

    conn.close()
    return render_template("subscribers.html", subs=subs, services=services, q=q, service_filter=service_filter)

@app.route("/subscribers/add", methods=["GET", "POST"])
@login_required
def subscriber_add():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM services ORDER BY id")
    services = c.fetchall()

    if request.method == "POST":
        name = request.form.get("name","").strip()
        phone = request.form.get("phone","").strip()
        address = request.form.get("address","").strip()
        service_id = request.form.get("service_id","").strip()
        start_date = request.form.get("start_date","").strip() or today_str()
        expiry_date = add_one_month(start_date)

        if not name or not service_id:
            flash("Name and Service are required", "danger")
            conn.close()
            return redirect(url_for("subscriber_add"))

        c.execute("""
        INSERT INTO subscribers (name, phone, address, service_id, start_date, expiry_date)
        VALUES (?,?,?,?,?,?)
        """, (name, phone, address, service_id, start_date, expiry_date))

        conn.commit()
        conn.close()
        flash("Subscriber added", "success")
        return redirect(url_for("subscribers"))

    conn.close()
    return render_template("subscriber_form.html", mode="add", services=services, sub=None)

@app.route("/subscribers/edit/<int:sid>", methods=["GET", "POST"])
@login_required
def subscriber_edit(sid):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM services ORDER BY id")
    services = c.fetchall()

    c.execute("SELECT * FROM subscribers WHERE id=?", (sid,))
    sub = c.fetchone()
    if not sub:
        conn.close()
        flash("Subscriber not found", "danger")
        return redirect(url_for("subscribers"))

    if request.method == "POST":
        name = request.form.get("name","").strip()
        phone = request.form.get("phone","").strip()
        address = request.form.get("address","").strip()
        service_id = request.form.get("service_id","").strip()
        start_date = request.form.get("start_date","").strip() or sub["start_date"]
        expiry_date = request.form.get("expiry_date","").strip() or sub["expiry_date"]

        if not name or not service_id:
            flash("Name and Service are required", "danger")
            conn.close()
            return redirect(url_for("subscriber_edit", sid=sid))

        c.execute("""
        UPDATE subscribers
        SET name=?, phone=?, address=?, service_id=?, start_date=?, expiry_date=?
        WHERE id=?
        """, (name, phone, address, service_id, start_date, expiry_date, sid))

        conn.commit()
        conn.close()
        flash("Subscriber updated", "success")
        return redirect(url_for("subscribers"))

    conn.close()
    return render_template("subscriber_form.html", mode="edit", services=services, sub=sub)

@app.route("/subscribers/delete/<int:sid>", methods=["POST"])
@login_required
def subscriber_delete(sid):
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM subscribers WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    flash("Subscriber deleted", "success")
    return redirect(url_for("subscribers"))

@app.route("/subscribers/renew/<int:sid>", methods=["POST"])
@login_required
def subscriber_renew(sid):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM subscribers WHERE id=?", (sid,))
    sub = c.fetchone()
    if not sub:
        conn.close()
        flash("Subscriber not found", "danger")
        return redirect(url_for("subscribers"))

    # نجدد شهر من تاريخ اليوم
    new_start = today_str()
    new_expiry = add_one_month(new_start)

    c.execute("UPDATE subscribers SET start_date=?, expiry_date=? WHERE id=?", (new_start, new_expiry, sid))
    conn.commit()
    conn.close()
    flash("Renewed for 30 days", "success")
    return redirect(url_for("subscribers"))

# =========================
# Services page (عرض فقط)
# =========================
# =========================
# Services Management
# =========================
# =========================
# Service Reports
# =========================
@app.route("/reports/services")
@login_required
def service_reports():
    conn = db()
    c = conn.cursor()

    c.execute("""
    SELECT sv.id, sv.name, sv.speed, sv.price,
           COUNT(s.id) as total_subs
    FROM services sv
    LEFT JOIN subscribers s ON s.service_id = sv.id
    GROUP BY sv.id
    ORDER BY sv.id
    """)
    services = c.fetchall()

    report_data = []

    for sv in services:
        c.execute("""
        SELECT expiry_date FROM subscribers
        WHERE service_id = ?
        """, (sv["id"],))
        subs = c.fetchall()

        active = 0
        expired = 0

        for sub in subs:
            if is_expired(sub["expiry_date"]):
                expired += 1
            else:
                active += 1

        income = active * float(sv["price"])

        report_data.append({
            "name": sv["name"],
            "speed": sv["speed"],
            "price": sv["price"],
            "total": sv["total_subs"],
            "active": active,
            "expired": expired,
            "income": income
        })

    conn.close()
    return render_template("service_report.html", report=report_data)
@app.route("/services/add", methods=["GET", "POST"])
@login_required
def service_add():
    if request.method == "POST":
        name = request.form.get("name")
        speed = request.form.get("speed")
        price = request.form.get("price")

        conn = db()
        c = conn.cursor()
        c.execute("INSERT INTO services (name, speed, price) VALUES (?,?,?)",
                  (name, speed, price))
        conn.commit()
        conn.close()

        flash("Service added successfully", "success")
        return redirect(url_for("services"))

    return render_template("service_form.html", mode="add", service=None)


@app.route("/services/edit/<int:sid>", methods=["GET", "POST"])
@login_required
def service_edit(sid):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM services WHERE id=?", (sid,))
    service = c.fetchone()

    if request.method == "POST":
        name = request.form.get("name")
        speed = request.form.get("speed")
        price = request.form.get("price")

        c.execute("UPDATE services SET name=?, speed=?, price=? WHERE id=?",
                  (name, speed, price, sid))
        conn.commit()
        conn.close()

        flash("Service updated successfully", "success")
        return redirect(url_for("services"))

    conn.close()
    return render_template("service_form.html", mode="edit", service=service)


@app.route("/services/delete/<int:sid>", methods=["POST"])
@login_required
def service_delete(sid):
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM services WHERE id=?", (sid,))
    conn.commit()
    conn.close()

    flash("Service deleted", "success")
    return redirect(url_for("services"))
@app.route("/services")
@login_required
def services():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM services ORDER BY id")
    services = c.fetchall()
    conn.close()
    return render_template("services.html", services=services)

if __name__ == "__main__":
    app.run()