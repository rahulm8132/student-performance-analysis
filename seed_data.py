"""Create deterministic synthetic college analytics data in MongoDB.

Run: python seed_data.py
To remove and recreate this demo database's project collections: python seed_data.py --reset
"""
import argparse
import os
import random
from datetime import date

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from werkzeug.security import generate_password_hash

load_dotenv()
SEED = 42

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "college_analytics")

DEPARTMENTS = ["Computer Engineering", "Information Technology", "AI & Data Science", "Electronics", "Mechanical", "Civil"]
SUBJECTS = [
    "Database Management Systems", "Computer Networks", "Operating Systems",
    "Data Structures", "Software Engineering", "Big Data Analytics",
    "Web Programming", "Artificial Intelligence", "Cloud Computing", "Cyber Security",
]
FIRST_NAMES = ["Aarav", "Aditi", "Aditya", "Ananya", "Arjun", "Diya", "Ishaan", "Kavya", "Neha", "Rohan", "Sai", "Sana", "Vedant", "Priya", "Om", "Meera", "Rahul", "Sneha", "Yash", "Pooja"]
LAST_NAMES = ["Patil", "Shinde", "More", "Naik", "Desai", "Kulkarni", "Jadhav", "Sawant", "Pawar", "Kadam", "Joshi", "Mane", "Chavan", "Pujari", "Rane"]
COMPANIES = ["BluePeak Systems", "Nexora Technologies", "BrightLayer Labs", "CloudNest Solutions", "DataSpring Analytics", "Vertex Digital", "Northstar Software", "OrbitStack", "PixelForge", "Cedarline Tech", "AsterByte", "Maplewave Consulting"]
ROLES = ["Software Developer", "Data Analyst", "QA Engineer", "Cloud Support Engineer", "Business Analyst", "Junior Data Engineer", "Network Engineer"]


def generate_dataset(student_count, seed=SEED):
    """Return synthetic students, attendance, examinations and placements (deterministic for a seed)."""
    rng = random.Random(seed)
    students, attendance, exams, placements = [], [], [], []
    today = date.today()
    for i in range(1, student_count + 1):
        department = DEPARTMENTS[(i - 1) % len(DEPARTMENTS)]
        batch = rng.choice([2022, 2023, 2024, 2025])
        semester = rng.choice([3, 4, 5, 6, 7, 8])
        cgpa = round(max(3.2, min(9.8, rng.gauss(7.1, 1.05))), 2)
        eligible = semester >= 7 and cgpa >= 6.0 and rng.random() > 0.08
        student_id = f"{department[:2].upper().replace(' ', '').replace('&', '')}{batch}{i:04d}"
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        student = {
            "student_id": student_id, "name": name, "department": department,
            "batch": batch, "semester": semester, "cgpa": cgpa,
            "placement_eligible": eligible, "active": True,
            "created_at": today.isoformat(),
        }
        students.append(student)
        subject_pool = rng.sample(SUBJECTS, 4)
        for subject in subject_pool:
            classes_held = rng.choice([28, 30, 32, 36, 40])
            percentage = round(max(35, min(100, rng.gauss(78, 12))), 1)
            classes_attended = round(classes_held * percentage / 100)
            actual_pct = round(classes_attended / classes_held * 100, 1)
            attendance.append({
                "student_id": student_id, "department": department, "subject": subject,
                "academic_year": f"{batch}-{batch + 1}", "classes_held": classes_held,
                "classes_attended": classes_attended, "attendance_percentage": actual_pct,
            })
            mark_pct = round(max(0, min(100, rng.gauss(62 + (cgpa - 6.5) * 4, 15))), 1)
            marks = round(mark_pct)
            exams.append({
                "student_id": student_id, "department": department, "semester": semester,
                "subject": subject, "academic_year": f"{batch}-{batch + 1}",
                "marks_obtained": marks, "maximum_marks": 100, "percentage": float(marks),
                "result": "Pass" if marks >= 40 else "Fail",
            })
        if eligible:
            r = rng.random()
            if r < 0.58:
                status = "Placed"
                company = rng.choice(COMPANIES)
                job_role = rng.choice(ROLES)
                salary = rng.randrange(300000, 1100000, 25000)
            elif r < 0.76:
                status, company, job_role, salary = "In Process", "", "", 0
            else:
                status, company, job_role, salary = "Not Placed", "", "", 0
            placements.append({
                "student_id": student_id, "department": department, "batch": batch,
                "academic_year": f"{batch}-{batch + 1}", "company": company,
                "job_role": job_role, "status": status, "annual_salary": salary,
                "updated_at": today.isoformat(),
            })
    return students, attendance, exams, placements


def main():
    parser = argparse.ArgumentParser(description="Seed synthetic college analytics data")
    parser.add_argument("--reset", action="store_true", help="drop and recreate the demo collections (destructive)")
    parser.add_argument("--students", type=int, default=500, help="number of synthetic students (default: 500)")
    args = parser.parse_args()
    if args.students < 30:
        parser.error("Use at least 30 students for meaningful demo analytics.")

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[MONGODB_DB]
    collection_names = ["students", "attendance", "examinations", "placements", "departments", "companies", "users", "analytics_results"]
    existing = sum(db[name].count_documents({}) for name in ["students", "attendance", "examinations", "placements"])
    if existing and not args.reset:
        print(f"The database already contains {existing} project records. Nothing was changed.")
        print("Run 'python seed_data.py --reset' only if you want to replace the existing demo data.")
        return
    if args.reset:
        for name in collection_names:
            db[name].drop()

    students, attendance, exams, placements = generate_dataset(args.students)

    db.students.insert_many(students, ordered=False)
    db.attendance.insert_many(attendance, ordered=False)
    db.examinations.insert_many(exams, ordered=False)
    if placements:
        db.placements.insert_many(placements, ordered=False)

    db.departments.insert_many([{"name": name, "code": f"D{idx:02d}"} for idx, name in enumerate(DEPARTMENTS, 1)])
    db.companies.insert_many([{"name": name, "industry": "Technology"} for name in COMPANIES])

    accounts = [
        (os.getenv("ADMIN_USERNAME", "admin"), os.getenv("ADMIN_PASSWORD", "Admin@1234"), "admin", "College Administrator"),
        (os.getenv("FACULTY_USERNAME", "faculty"), os.getenv("FACULTY_PASSWORD", "Faculty@1234"), "faculty", "Faculty Member"),
        (os.getenv("PLACEMENT_USERNAME", "placement"), os.getenv("PLACEMENT_PASSWORD", "Placement@1234"), "placement", "Placement Officer"),
    ]
    db.users.insert_many([
        {"username": username, "password_hash": generate_password_hash(password), "role": role,
         "display_name": display_name, "active": True}
        for username, password, role, display_name in accounts
    ])

    db.students.create_index([("student_id", ASCENDING)], unique=True)
    db.attendance.create_index([("student_id", ASCENDING), ("subject", ASCENDING), ("academic_year", ASCENDING)], unique=True)
    db.examinations.create_index([("student_id", ASCENDING), ("semester", ASCENDING), ("subject", ASCENDING), ("academic_year", ASCENDING)], unique=True)
    db.placements.create_index([("student_id", ASCENDING), ("academic_year", ASCENDING)], unique=True)
    db.users.create_index([("username", ASCENDING)], unique=True)

    print("\nDemo data seeded successfully.")
    print(f"Database: {MONGODB_DB}")
    print(f"Students: {len(students):,}")
    print(f"Attendance records: {len(attendance):,}")
    print(f"Examination records: {len(exams):,}")
    print(f"Placement records: {len(placements):,}")
    print("\nDemo logins (change these passwords before using outside a local demo):")
    for username, password, role, _ in accounts:
        print(f"  {role:9s}  username={username}  password={password}")


if __name__ == "__main__":
    main()
