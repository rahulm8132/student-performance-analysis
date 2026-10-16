"""CampusInsight - College Student Performance & Placement Analytics (Flask + MongoDB)."""
import csv
import hmac
import io
import logging
import os
import re
import secrets
import time
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask, Response, abort, flash, redirect, render_template, request,
    session, url_for,
)
from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash

load_dotenv()

DEFAULT_SECRET = "dev-only-change-this-secret"
PASS_RESULT = "Pass"
PLACED = "Placed"
IN_PROCESS = "In Process"
NOT_PLACED = "Not Placed"
PLACEMENT_STATUSES = (PLACED, NOT_PLACED, IN_PROCESS)
STUDENTS_PER_PAGE = 25
DEFAULT_ATTENDANCE_THRESHOLD = 65.0
MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 300

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", DEFAULT_SECRET),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    ATTENDANCE_THRESHOLD=float(os.getenv("ATTENDANCE_THRESHOLD", DEFAULT_ATTENDANCE_THRESHOLD)),
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("campusinsight")
if app.config["SECRET_KEY"] == DEFAULT_SECRET:
    log.warning("SECRET_KEY is not set; using an insecure development default.")

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "college_analytics")
client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=2500, connectTimeoutMS=2500)
db = client[MONGODB_DB]

COLLECTIONS = {
    "students": db.students,
    "attendance": db.attendance,
    "examinations": db.examinations,
    "placements": db.placements,
}
DATASET_FIELDS = {
    "students": ["student_id", "name", "department", "batch", "semester", "cgpa", "placement_eligible", "active"],
    "attendance": ["student_id", "department", "subject", "academic_year", "classes_held", "classes_attended", "attendance_percentage"],
    "examinations": ["student_id", "department", "semester", "subject", "academic_year", "marks_obtained", "maximum_marks", "percentage", "result"],
    "placements": ["student_id", "department", "batch", "academic_year", "company", "job_role", "status", "annual_salary"],
}
# Key fields used to upsert imported rows so re-importing a file never duplicates records.
IMPORT_KEYS = {
    "students": ("student_id",),
    "attendance": ("student_id", "subject", "academic_year"),
    "examinations": ("student_id", "semester", "subject", "academic_year"),
    "placements": ("student_id", "academic_year"),
}
# Which roles may read or export each dataset.
DATASET_ROLES = {
    "students": ("admin", "faculty", "placement"),
    "attendance": ("admin", "faculty"),
    "examinations": ("admin", "faculty"),
    "placements": ("admin", "placement"),
}

_failed_logins = {}  # (ip, username) -> (count, first_failure_time)


# ---------------------------------------------------------------- security helpers
def get_role():
    return session.get("role", "")


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def verify_csrf():
    if request.method != "POST":
        return None
    sent = request.form.get("_csrf", "")
    if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
        log.warning("Rejected POST to %s: missing or invalid CSRF token", request.path)
        abort(400, description="Invalid or missing form token. Reload the page and try again.")
    return None


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'",
    )
    return response


def login_blocked(key):
    count, first = _failed_logins.get(key, (0, 0.0))
    if count >= MAX_LOGIN_FAILURES and time.time() - first < LOCKOUT_SECONDS:
        return True
    if count and time.time() - first >= LOCKOUT_SECONDS:
        _failed_logins.pop(key, None)
    return False


def record_login_failure(key):
    count, first = _failed_logins.get(key, (0, time.time()))
    _failed_logins[key] = (count + 1, first)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*allowed_roles):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(*args, **kwargs):
            if get_role() not in allowed_roles:
                flash("Your account does not have permission to open that page.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------- analytics helpers
def pass_rate_stages(group_key, name):
    """Return the $group + $project stages giving average score, record count and pass count.

    Rounding and the pass-rate division are done in Python (see finish_pass_rates) so the
    pipelines only use operators available in every MongoDB version.
    """
    return [
        {"$group": {
            "_id": f"${group_key}",
            "average_score": {"$avg": "$percentage"},
            "records": {"$sum": 1},
            "passed": {"$sum": {"$cond": [{"$eq": ["$result", PASS_RESULT]}, 1, 0]}},
        }},
        {"$project": {"_id": 0, name: "$_id", "average_score": 1, "records": 1, "passed": 1}},
    ]


def finish_pass_rates(rows):
    for row in rows:
        row["average_score"] = round(float(row.get("average_score") or 0), 1)
        row["pass_rate"] = percent(row.get("passed", 0), row.get("records", 0))
    return rows


def safe_average(collection, field):
    result = list(collection.aggregate([{"$group": {"_id": None, "average": {"$avg": f"${field}"}}}]))
    if result and result[0].get("average") is not None:
        return round(float(result[0]["average"]), 2)
    return 0.0


def percent(part, whole):
    return round(part / whole * 100, 1) if whole else 0.0


def department_academic_summary(match=None):
    pipeline = [{"$match": match}] if match else []
    pipeline += pass_rate_stages("department", "department") + [{"$sort": {"department": 1}}]
    return finish_pass_rates(list(db.examinations.aggregate(pipeline)))


def department_attendance_summary():
    rows = list(db.attendance.aggregate([
        {"$group": {"_id": "$department", "average_attendance": {"$avg": "$attendance_percentage"}, "records": {"$sum": 1}}},
        {"$project": {"_id": 0, "department": "$_id", "average_attendance": 1, "records": 1}},
        {"$sort": {"department": 1}},
    ]))
    for row in rows:
        row["average_attendance"] = round(float(row["average_attendance"] or 0), 1)
    return rows


def department_placement_summary():
    eligible_map = {item["_id"]: item["eligible"] for item in db.students.aggregate([
        {"$match": {"placement_eligible": True}},
        {"$group": {"_id": "$department", "eligible": {"$sum": 1}}},
    ])}
    placement_map = {item["_id"]: item for item in db.placements.aggregate([
        {"$group": {
            "_id": "$department",
            "placed": {"$sum": {"$cond": [{"$eq": ["$status", PLACED]}, 1, 0]}},
            "in_process": {"$sum": {"$cond": [{"$eq": ["$status", IN_PROCESS]}, 1, 0]}},
            "average_salary": {"$avg": {"$cond": [{"$and": [{"$eq": ["$status", PLACED]}, {"$gt": ["$annual_salary", 0]}]}, "$annual_salary", None]}},
        }},
    ])}
    rows = []
    for dept in sorted(set(eligible_map) | set(placement_map)):
        placement = placement_map.get(dept, {})
        eligible = int(eligible_map.get(dept, 0))
        placed = int(placement.get("placed", 0))
        rows.append({
            "department": dept, "eligible": eligible, "placed": placed,
            "in_process": int(placement.get("in_process", 0)),
            "placement_rate": percent(placed, eligible),
            "average_salary": round(float(placement.get("average_salary") or 0), 0),
        })
    return rows


def status_counts():
    return list(db.placements.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]))


def chart_payload(rows, label_key, value_key):
    return {
        "labels": [str(row.get(label_key, "Unknown") or "Unknown") for row in rows],
        "values": [round(float(row.get(value_key) or 0), 2) for row in rows],
    }


def status_payload(rows):
    return {"labels": [r["_id"] for r in rows], "values": [r["count"] for r in rows]}


def parse_threshold(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return app.config["ATTENDANCE_THRESHOLD"]
    return min(max(value, 0.0), 100.0)


@app.context_processor
def inject_globals():
    return {"logged_in_user": session.get("user_id"), "logged_in_role": get_role(), "csrf_token": csrf_token}


# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    return redirect(url_for("dashboard" if session.get("user_id") else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        password = request.form.get("password", "")
        key = (request.remote_addr, username.lower())
        if login_blocked(key):
            flash("Too many failed attempts. Please wait a few minutes and try again.", "error")
            return render_template("login.html"), 429
        try:
            user = db.users.find_one({"username": username, "active": True})
        except PyMongoError:
            return render_template("db_error.html"), 503
        if user and check_password_hash(user.get("password_hash", ""), password):
            _failed_logins.pop(key, None)
            session.clear()
            session["user_id"] = user["username"]
            session["role"] = user.get("role", "viewer")
            log.info("User %s signed in with role %s", user["username"], session["role"])
            flash(f"Welcome back, {user.get('display_name', username)}.", "success")
            target = request.args.get("next", "")
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("dashboard")
            return redirect(target)
        record_login_failure(key)
        log.warning("Failed login for username %r", username)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    try:
        total_students = db.students.count_documents({"active": {"$ne": False}})
        attendance_rows = list(db.attendance.aggregate([
            {"$group": {"_id": None, "held": {"$sum": "$classes_held"}, "attended": {"$sum": "$classes_attended"}}}
        ]))
        held = int(attendance_rows[0].get("held", 0)) if attendance_rows else 0
        attended = int(attendance_rows[0].get("attended", 0)) if attendance_rows else 0
        eligible = db.students.count_documents({"placement_eligible": True})
        placed = db.placements.count_documents({"status": PLACED})
        in_process = db.placements.count_documents({"status": IN_PROCESS})
        academic_rows = department_academic_summary()
        placement_rows = department_placement_summary()
        status_rows = status_counts()
        recent_placements = list(db.placements.find({"status": PLACED}, {
            "_id": 0, "student_id": 1, "department": 1, "company": 1, "job_role": 1, "annual_salary": 1,
        }).sort("annual_salary", -1).limit(6))
        datasets = {name: collection.count_documents({}) for name, collection in COLLECTIONS.items()}
        avg_cgpa = safe_average(db.students, "cgpa")
    except PyMongoError:
        return render_template("db_error.html"), 503
    return render_template(
        "dashboard.html", active_page="dashboard", total_students=total_students,
        avg_cgpa=avg_cgpa, avg_attendance=percent(attended, held), eligible=eligible,
        placed=placed, in_process=in_process, placement_rate=percent(placed, eligible),
        academic_rows=academic_rows, placement_rows=placement_rows,
        status_rows=status_rows, recent_placements=recent_placements, datasets=datasets,
        department_chart=chart_payload(academic_rows, "department", "average_score"),
        placement_chart=chart_payload(placement_rows, "department", "placement_rate"),
        status_chart=status_payload(status_rows),
    )


@app.route("/students")
@roles_required(*DATASET_ROLES["students"])
def students():
    department = request.args.get("department", "").strip()[:80]
    q = request.args.get("q", "").strip()[:80]
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    query = {"active": {"$ne": False}}
    if department:
        query["department"] = department
    if q:
        pattern = re.escape(q)
        query["$or"] = [
            {"student_id": {"$regex": pattern, "$options": "i"}},
            {"name": {"$regex": pattern, "$options": "i"}},
        ]
    try:
        total_matching = db.students.count_documents(query)
        pages = max((total_matching + STUDENTS_PER_PAGE - 1) // STUDENTS_PER_PAGE, 1)
        page = min(page, pages)
        rows = list(db.students.find(query, {"_id": 0}).sort([("department", 1), ("student_id", 1)])
                    .skip((page - 1) * STUDENTS_PER_PAGE).limit(STUDENTS_PER_PAGE))
        departments = sorted(db.students.distinct("department"))
    except PyMongoError:
        return render_template("db_error.html"), 503
    return render_template("students.html", active_page="students", students=rows,
                           departments=departments, selected_department=department, search_q=q,
                           total_matching=total_matching, page=page, pages=pages, per_page=STUDENTS_PER_PAGE)


@app.route("/academics")
@roles_required(*DATASET_ROLES["examinations"])
def academics():
    selected_semester = request.args.get("semester", "")
    match = {"semester": int(selected_semester)} if selected_semester.isdigit() else {}
    try:
        semesters = sorted(db.examinations.distinct("semester"))
        rows = department_academic_summary(match)
        subject_pipeline = ([{"$match": match}] if match else []) + pass_rate_stages("subject", "subject") + [
            {"$sort": {"average_score": 1}}, {"$limit": 12}]
        subject_rows = finish_pass_rates(list(db.examinations.aggregate(subject_pipeline)))
        examination_count = db.examinations.count_documents(match)
    except PyMongoError:
        return render_template("db_error.html"), 503
    return render_template("academics.html", active_page="academics", rows=rows, subject_rows=subject_rows,
                           semesters=semesters, selected_semester=selected_semester,
                           examination_count=examination_count,
                           score_chart=chart_payload(rows, "department", "average_score"),
                           pass_chart=chart_payload(subject_rows, "subject", "pass_rate"))


@app.route("/attendance")
@roles_required(*DATASET_ROLES["attendance"])
def attendance():
    threshold = parse_threshold(request.args.get("threshold"))
    try:
        rows = department_attendance_summary()
        flagged_query = {"attendance_percentage": {"$lt": threshold}}
        flagged_total = db.attendance.count_documents(flagged_query)
        low_attendance = list(db.attendance.find(flagged_query, {
            "_id": 0, "student_id": 1, "department": 1, "subject": 1, "attendance_percentage": 1,
        }).sort("attendance_percentage", 1).limit(15))
        count = db.attendance.count_documents({})
        overall = safe_average(db.attendance, "attendance_percentage")
    except PyMongoError:
        return render_template("db_error.html"), 503
    return render_template("attendance.html", active_page="attendance", rows=rows, low_attendance=low_attendance,
                           count=count, overall=overall, threshold=threshold, flagged_total=flagged_total,
                           attendance_chart=chart_payload(rows, "department", "average_attendance"))


@app.route("/placements")
@roles_required(*DATASET_ROLES["placements"])
def placements():
    try:
        rows = department_placement_summary()
        statuses = status_counts()
        companies = list(db.placements.aggregate([
            {"$match": {"status": PLACED, "company": {"$ne": ""}}},
            {"$group": {"_id": "$company", "hires": {"$sum": 1}, "average_salary": {"$avg": "$annual_salary"}}},
            {"$sort": {"hires": -1, "_id": 1}}, {"$limit": 10},
            {"$project": {"_id": 0, "company": "$_id", "hires": 1, "average_salary": 1}},
        ]))
        for company in companies:
            company["average_salary"] = round(float(company["average_salary"] or 0))
        placed = db.placements.count_documents({"status": PLACED})
        eligible = db.students.count_documents({"placement_eligible": True})
        salary_rows = list(db.placements.aggregate([
            {"$match": {"status": PLACED, "annual_salary": {"$gt": 0}}},
            {"$group": {"_id": None, "avg": {"$avg": "$annual_salary"}, "max": {"$max": "$annual_salary"}}},
        ]))
    except PyMongoError:
        return render_template("db_error.html"), 503
    avg_salary = round(salary_rows[0]["avg"]) if salary_rows else 0
    max_salary = round(salary_rows[0]["max"]) if salary_rows else 0
    return render_template("placements.html", active_page="placements", rows=rows, statuses=statuses,
                           companies=companies, placed=placed, eligible=eligible, rate=percent(placed, eligible),
                           avg_salary=avg_salary, max_salary=max_salary,
                           placement_chart=chart_payload(rows, "department", "placement_rate"),
                           status_chart=status_payload(statuses))


# ---------------------------------------------------------------- CSV import / export
def normalize_import_record(dataset, doc):
    def to_int(value, name):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{name}' must be a whole number; received {value!r}.")

    def to_float(value, name):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{name}' must be numeric; received {value!r}.")

    if dataset == "students":
        doc["batch"] = to_int(doc["batch"], "batch")
        doc["semester"] = to_int(doc["semester"], "semester")
        doc["cgpa"] = to_float(doc["cgpa"], "cgpa")
        if not 0 <= doc["cgpa"] <= 10:
            raise ValueError("cgpa must be between 0 and 10.")
        doc["placement_eligible"] = doc["placement_eligible"].lower() in ("true", "1", "yes", "y")
        doc["active"] = doc["active"].lower() not in ("false", "0", "no", "n")
    elif dataset == "attendance":
        doc["classes_held"] = to_int(doc["classes_held"], "classes_held")
        doc["classes_attended"] = to_int(doc["classes_attended"], "classes_attended")
        if doc["classes_held"] <= 0 or not 0 <= doc["classes_attended"] <= doc["classes_held"]:
            raise ValueError("Attendance counts must satisfy 0 <= classes_attended <= classes_held (and classes_held > 0).")
        # Always derive the percentage from the counts so the stored value cannot disagree with them.
        doc["attendance_percentage"] = round(doc["classes_attended"] / doc["classes_held"] * 100, 1)
    elif dataset == "examinations":
        doc["semester"] = to_int(doc["semester"], "semester")
        doc["marks_obtained"] = to_float(doc["marks_obtained"], "marks_obtained")
        doc["maximum_marks"] = to_float(doc["maximum_marks"], "maximum_marks")
        if doc["maximum_marks"] <= 0 or not 0 <= doc["marks_obtained"] <= doc["maximum_marks"]:
            raise ValueError("Exam marks must be between 0 and maximum_marks, which must be positive.")
        doc["percentage"] = round(doc["marks_obtained"] / doc["maximum_marks"] * 100, 1)
        doc["result"] = PASS_RESULT if doc["result"].lower() == "pass" else "Fail"
    elif dataset == "placements":
        doc["batch"] = to_int(doc["batch"], "batch")
        salary = doc["annual_salary"]
        doc["annual_salary"] = to_int(salary, "annual_salary") if salary else 0
        if doc["annual_salary"] < 0:
            raise ValueError("annual_salary cannot be negative.")
        doc["status"] = doc["status"].title()
        if doc["status"] not in PLACEMENT_STATUSES:
            raise ValueError("Placement status must be Placed, Not Placed, or In Process.")
    return doc


def parse_csv_upload(dataset, uploaded):
    content = uploaded.stream.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError("The CSV file appears to be empty.")
    missing = set(DATASET_FIELDS[dataset]) - set(reader.fieldnames)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
    records = []
    for line_num, raw in enumerate(reader, start=2):
        doc = {field: str(raw.get(field) or "").strip() for field in DATASET_FIELDS[dataset]}
        if not doc.get("student_id"):
            continue
        try:
            records.append(normalize_import_record(dataset, doc))
        except ValueError as exc:
            raise ValueError(f"Row {line_num}: {exc}")
    if not records:
        raise ValueError("No valid rows were found in the CSV file.")
    return records


@app.route("/data-tools", methods=["GET", "POST"])
@roles_required("admin")
def data_tools():
    if request.method == "POST":
        dataset = request.form.get("dataset", "")
        uploaded = request.files.get("csv_file")
        if dataset not in COLLECTIONS:
            flash("Choose a valid dataset type.", "error")
        elif not uploaded or not uploaded.filename.lower().endswith(".csv"):
            flash("Please select a CSV file.", "error")
        else:
            try:
                records = parse_csv_upload(dataset, uploaded)
                keys = IMPORT_KEYS[dataset]
                operations = [UpdateOne({k: doc[k] for k in keys}, {"$set": doc}, upsert=True) for doc in records]
                result = COLLECTIONS[dataset].bulk_write(operations, ordered=False)
                log.info("%s imported %d %s rows", session.get("user_id"), len(records), dataset)
                flash(f"Imported {len(records)} {dataset} rows ({result.upserted_count} inserted, {result.modified_count} updated).", "success")
            except (UnicodeDecodeError, csv.Error, ValueError, KeyError, TypeError) as exc:
                flash(f"Import failed: {exc}", "error")
            except PyMongoError:
                return render_template("db_error.html"), 503
        return redirect(url_for("data_tools"))
    dataset_counts = {}
    for name, collection in COLLECTIONS.items():
        try:
            dataset_counts[name] = collection.count_documents({})
        except PyMongoError:
            dataset_counts[name] = 0
    return render_template("data_tools.html", active_page="data-tools", dataset_counts=dataset_counts,
                           dataset_fields=DATASET_FIELDS)


@app.route("/export/<dataset>.csv")
@login_required
def export_csv(dataset):
    if dataset not in COLLECTIONS:
        abort(404)
    if get_role() not in DATASET_ROLES[dataset]:
        abort(403)
    try:
        docs = list(COLLECTIONS[dataset].find({}, {"_id": 0}).limit(100000))
    except PyMongoError:
        return render_template("db_error.html"), 503
    fields = DATASET_FIELDS[dataset]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for doc in docs:
        writer.writerow({field: doc.get(field, "") for field in fields})
    return Response(stream.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={dataset}_export.csv"})


# ---------------------------------------------------------------- errors / health
@app.errorhandler(413)
def too_large(_error):
    flash("The uploaded file is too large. Maximum size is 5 MB.", "error")
    return redirect(url_for("data_tools"))


@app.errorhandler(PyMongoError)
def database_problem(_error):
    return render_template("db_error.html"), 503


@app.route("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok", "database": MONGODB_DB, "mongodb": "connected"}
    except PyMongoError:
        return {"status": "error", "database": MONGODB_DB, "mongodb": "disconnected"}, 503


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true", host="127.0.0.1", port=5000)
