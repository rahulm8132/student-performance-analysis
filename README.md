# College Student Performance & Placement Analytics

A local web application built with **Python, Flask and MongoDB**. It analyzes synthetic student records, attendance, examination results and placements, then presents interactive dashboards. An optional PySpark script demonstrates distributed processing over CSV data.

> **Demo data only:** `seed_data.py` creates synthetic student information. Do not import real student data unless you have authorization and have configured appropriate access controls.

## Features

- Role-based demo accounts: administrator, faculty and placement officer.
- Dashboard cards for student count, average CGPA, attendance and placement rate.
- Student directory with department filter and ID/name search.
- Academic analytics by department, subject and semester.
- Attendance trends and a list of records below a configurable threshold (default 65%; change it on the Attendance page or with `ATTENDANCE_THRESHOLD` in `.env`).
- Placement rate, status distribution, recruiter hiring summary and salary metrics.
- CSV import for students, attendance, examinations and placements (up to 5 MB per file).
- CSV export for analytics datasets.
- MongoDB aggregation pipelines, indexes and data validation.
- Optional PySpark CSV analytics script for large exported datasets.

## Requirements

- Windows 10/11, macOS or Linux
- Python 3.10 or newer (Python 3.11 is a good choice)
- MongoDB Community Server running locally **or** a MongoDB Atlas connection string
- No internet access needed for charts: Chart.js 4.4.7 is bundled in `static/js/vendor/` (web fonts are optional and fall back to system fonts)
- Java is required only for the optional PySpark script

## Run on Windows

Open Command Prompt or the VS Code terminal in this project folder.

### 1. Create a virtual environment

```bat
py -m venv .venv
.venv\Scripts\activate
```

If `py` is not recognized, install Python from python.org and enable **Add Python to PATH** during installation.

### 2. Install dependencies

```bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Configure MongoDB

Copy `.env.example` to `.env` and confirm the values. For a local MongoDB installation, the defaults are:

```ini
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DB=college_analytics
SECRET_KEY=replace-this-with-a-long-random-secret
```

Make sure the MongoDB service is running before continuing. To use MongoDB Atlas instead, put your Atlas connection string in `MONGODB_URI` and configure the database user's credentials and network access in Atlas. Do not commit `.env` or share your database password.

### 4. Generate the demo dataset

```bat
python seed_data.py
```

This inserts 500 synthetic students by default, with attendance, exam results, and placement records. The script stops without changing data if it detects an existing dataset. To replace the project's demo collections, run:

```bat
python seed_data.py --reset
```

**Warning:** `--reset` deletes and recreates the project collections in the configured database. Do not run it against a database containing real or valuable data.

To make a larger dataset for testing:

```bat
python seed_data.py --students 5000
```

Use `--reset` if you already have the seeded records and want to regenerate with a different number of students.

### 5. Start the application

```bat
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Check the database connection at **http://127.0.0.1:5000/health**.

## Demo accounts

| Role | Username | Password |
|---|---|---|
| Administrator | `admin` | `Admin@1234` |
| Faculty | `faculty` | `Faculty@1234` |
| Placement officer | `placement` | `Placement@1234` |

Credentials can be changed in `.env` before running `seed_data.py`. If changing accounts after seeding, use a fresh demo database or manage the user records carefully. These are demo passwords; change them before any shared or deployed use.

## CSV import format

Use UTF-8 CSV files with the exact headers below. Every dataset needs a `student_id`; field names are case-sensitive.

**students.csv**

```csv
student_id,name,department,batch,semester,cgpa,placement_eligible,active
CE20250001,Aarav Patil,Computer Engineering,2025,5,7.4,false,true
```

**attendance.csv**

```csv
student_id,department,subject,academic_year,classes_held,classes_attended,attendance_percentage
CE20250001,Computer Engineering,Database Management Systems,2025-2026,30,25,83.3
```

**examinations.csv**

```csv
student_id,department,semester,subject,academic_year,marks_obtained,maximum_marks,percentage,result
CE20250001,Computer Engineering,5,Database Management Systems,2025-2026,78,100,78,Pass
```

**placements.csv**

```csv
student_id,department,batch,academic_year,company,job_role,status,annual_salary
CE20250001,Computer Engineering,2025,2026-2027,Example Technologies,Software Developer,Placed,500000
```

Import uses upsert keys to update matching records rather than blindly duplicating them. Keep the keys unique: student ID for students; student ID + subject + academic year for attendance; student ID + semester + subject + academic year for exams; and student ID + academic year for placements.

## Optional Apache Spark demonstration

Spark is **not needed** to run the Flask dashboard. To demonstrate distributed analytics, install Java and PySpark in a compatible environment:

```bat
python -m pip install pyspark
```

Export examination records from **Data Tools** in the application, then run:

```bat
python spark_analytics.py path\to\examinations_export.csv
```

The script groups exam data by department, computes average marks and pass rate, prints the results, and writes Spark output to `spark_output/department_academics`. For a meaningful scale comparison, test it with progressively larger CSV exports and record the measured runtimes. Spark/Java compatibility can vary by installation; the core application does not depend on this optional step.

## Running the tests

The test suite runs the Flask app against an in-memory MongoDB (`mongomock`), so no database server is needed:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

It covers login, CSRF protection, login lockout, role-based access, analytics results versus manual calculations, the attendance threshold, pagination and CSV import validation.

## Security notes

- Forms are protected with a per-session CSRF token; failed logins are rate-limited (5 attempts, 5-minute lockout per IP and username).
- Responses carry `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and a Content-Security-Policy header.
- Set `SECRET_KEY` to a long random value, change the demo passwords, and set `SESSION_COOKIE_SECURE=true` when serving over HTTPS.
- Imported rows are validated, and percentages are always recalculated from the underlying counts/marks.

## Project structure

```text
college_student_analytics_project/
├── app.py
├── seed_data.py
├── spark_analytics.py
├── requirements.txt
├── requirements-dev.txt
├── tests/
│   ├── conftest.py
│   └── test_app.py
├── .env.example
├── README.md
├── sample_data/
│   ├── students_template.csv
│   ├── attendance_template.csv
│   ├── examinations_template.csv
│   └── placements_template.csv
├── static/
│   ├── css/style.css
│   └── js/
│       ├── charts.js
│       └── vendor/chart.umd.js
└── templates/
    ├── base.html
    ├── login.html
    ├── db_error.html
    ├── dashboard.html
    ├── students.html
    ├── academics.html
    ├── attendance.html
    ├── placements.html
    └── data_tools.html
```

## Troubleshooting

- **MongoDB disconnected:** start the MongoDB service and verify `MONGODB_URI` and `MONGODB_DB` in `.env`; open `/health` to verify.
- **`No module named flask` / `pymongo`:** activate `.venv`, then rerun `python -m pip install -r requirements.txt`.
- **Port 5000 already in use:** close the other process using it, or change the port in the final `app.run(...)` line of `app.py`.
- **Charts do not load:** check that `static/js/vendor/chart.umd.js` exists and that your browser console shows no errors. The data tables still work if charts are unavailable.
- **Login fails:** run `python seed_data.py` after setting the desired demo credentials in `.env`. If users were already seeded, changing `.env` alone does not update existing stored password hashes.

## Academic use and limitations

The seeded data is synthetic and intended for software demonstrations. Predictive models are not included in the core application, and the placement/academic dashboards are descriptive analytics rather than evidence of causation. Before drawing institutional conclusions, validate the input data and check the definitions used for eligibility, attendance and placement rates. The demo app is intended for localhost use, not deployment as-is to the public internet.

## Standalone dashboard (no database needed)

Open `standalone_dashboard.html` in any browser (internet needed once for the Chart.js and font CDNs). It shows the same dashboard, students, academics, attendance, placements and data-import views using generated demo data. Imports last until the page is reloaded. The Flask + MongoDB app above is unchanged.
