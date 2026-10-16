"""Optional PySpark demonstration for larger data volumes.

Processes a CSV export of examination records with Spark and writes an aggregated
department report (average score, record count and pass rate). The Flask application
itself runs with MongoDB only and does not need Spark.

Prerequisites: Java 8+ and PySpark installed separately.
  python -m pip install pyspark
  python spark_analytics.py sample_data/examinations_template.csv
For meaningful results, use the examinations export from the app's Data Tools page.
"""
import argparse
import sys
from pathlib import Path

REQUIRED_COLUMNS = {"department", "percentage", "result"}

try:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
except ImportError as exc:
    raise SystemExit("PySpark is not installed. Run: python -m pip install pyspark") from exc


def build_summary(exams):
    """Group examination records by department and compute the three headline metrics."""
    return exams.groupBy("department").agg(
        F.round(F.avg(F.col("percentage").cast("double")), 2).alias("average_score"),
        F.count("*").alias("exam_records"),
        F.round(F.avg(F.when(F.col("result") == "Pass", 1.0).otherwise(0.0)) * 100, 2).alias("pass_rate_percent"),
    ).orderBy("department")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Department-wise examination summary with PySpark")
    parser.add_argument("input", nargs="?", default="sample_data/examinations_template.csv",
                        help="examinations CSV (default: sample_data/examinations_template.csv)")
    parser.add_argument("--output", default="spark_output/department_academics",
                        help="output folder for the summary CSV (default: spark_output/department_academics)")
    args = parser.parse_args(argv)

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"CSV file not found: {path}")

    spark = SparkSession.builder.appName("CollegeStudentAnalytics").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        exams = spark.read.option("header", True).option("inferSchema", True).csv(str(path.resolve()))
        missing = REQUIRED_COLUMNS - set(exams.columns)
        if missing:
            raise SystemExit("CSV is missing columns: " + ", ".join(sorted(missing)))
        summary = build_summary(exams)
        print(f"\nInput file : {path}")
        print(f"Records    : {exams.count():,}\n")
        summary.show(truncate=False)
        output_dir = Path(args.output)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        summary.coalesce(1).write.mode("overwrite").option("header", True).csv(str(output_dir))
        print(f"Spark summary saved to {output_dir.resolve()}")
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
