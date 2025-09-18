import os
import csv
import subprocess
import threading
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

GH_SOURCE_PAT = os.getenv("GH_SOURCE_PAT")
GH_PAT = os.getenv("GH_PAT")
SOURCE_ORG = os.getenv("SOURCE")
DESTINATION_ORG = os.getenv("DESTINATION")
TARGET_API_URL = os.getenv("TARGET_API_URL", "https://api.github.com")

# Validate environment
for var_name, var_value in [("GH_SOURCE_PAT", GH_SOURCE_PAT), ("GH_PAT", GH_PAT),
                            ("SOURCE", SOURCE_ORG), ("DESTINATION", DESTINATION_ORG)]:
    if not var_value:
        print(f"❌ Environment variable {var_name} not set. Exiting.")
        exit(1)

# Check CSV file exists
CSV_FILE = os.path.join(os.getcwd(), "repos.csv")
if not os.path.exists(CSV_FILE):
    print(f"❌ CSV file not found at {CSV_FILE}. Exiting.")
    exit(1)

# Setup logging
logging.basicConfig(
    filename="migration_errors.log",
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

OUTPUT_FILE = "MigrationDetails.csv"

# gh CLI
GH_CLI = "gh"  # if not in PATH, set full path like r"C:\Program Files\GitHub CLI\gh.exe"


def run_with_progress(cmd):
    """Run shell command while printing 'migration in progress...' every 5 seconds."""
    stop_flag = threading.Event()

    def printer():
        while not stop_flag.is_set():
            print("migration in progress...")
            time.sleep(5)

    t = threading.Thread(target=printer)
    t.start()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True, check=True)
        success, output = True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        success, output = False, e.stderr.strip()
        logging.error(f"Command failed: {e.stderr.strip()}")
    finally:
        stop_flag.set()
        t.join()

    return success, output


def migrate_repos(csv_file):
    # Read CSV and filter only valid rows
    with open(csv_file, newline="") as f:
        reader = csv.DictReader(f)
        repo_list = [row for row in reader if row.get("CURRENT-NAME", "").strip()]

    total = len(repo_list)
    completed = 0
    results = []

    for row in repo_list:
        source_repo = row["CURRENT-NAME"].strip()
        target_repo = row["NEW-NAME"].strip()

        print(f"\n🔄 Starting migration: {SOURCE_ORG}/{source_repo} → {DESTINATION_ORG}/{target_repo}")

        cmd = (
            f'"{GH_CLI}" gei migrate-repo '
            f'--github-source-org "{SOURCE_ORG}" '
            f'--source-repo "{source_repo}" '
            f'--github-target-org "{DESTINATION_ORG}" '
            f'--target-repo "{target_repo}" '
            f'--target-api-url "{TARGET_API_URL}"'
        )

        start_time = datetime.now(timezone.utc)
        success, output = run_with_progress(cmd)
        end_time = datetime.now(timezone.utc)

        duration_seconds = (end_time - start_time).total_seconds()
        duration_minutes = round(duration_seconds / 60, 2)

        completed += 1
        status_msg = "✅ Completed" if success else "❌ Failed"
        print(f"{status_msg} {source_repo} → {target_repo} ({completed}/{total}) "
              f"in {round(duration_seconds, 2)}s")

        results.append({
            "SourceOrg": SOURCE_ORG,
            "SourceRepo": source_repo,
            "TargetOrg": DESTINATION_ORG,
            "TargetRepo": target_repo,
            "Status": "Success" if success else "Failed",
            "StartTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "EndTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "TimeTakenSeconds": round(duration_seconds, 2),
            "TimeTakenMinutes": duration_minutes
        })

    # Write output CSV
    with open(OUTPUT_FILE, "w", newline="") as csvout:
        fieldnames = ["SourceOrg","SourceRepo","TargetOrg","TargetRepo",
                      "Status","StartTime","EndTime","TimeTakenSeconds","TimeTakenMinutes"]
        writer = csv.DictWriter(csvout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n🎯 All migrations finished. {completed}/{total} repos processed.")
    print(f"   Details → {OUTPUT_FILE}")
    print(f"   Errors  → migration_errors.log")


if __name__ == "__main__":
    migrate_repos(CSV_FILE)
