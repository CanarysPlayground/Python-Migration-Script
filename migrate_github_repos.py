#!/usr/bin/env python3
# migrate_github_repos.py

import os
import sys
import csv
import subprocess
import threading
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
import re

# ---------- Console & process encoding ----------
# Make stdout/stderr UTF-8 (prevents Windows cp1252 crashes on emoji/unicode)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
# ------------------------------------------------

# Load environment variables from .env (if present)
load_dotenv()

GH_SOURCE_PAT = os.getenv("GH_SOURCE_PAT")
GH_PAT = os.getenv("GH_PAT")
SOURCE_ORG = os.getenv("SOURCE")
DESTINATION_ORG = os.getenv("DESTINATION")
TARGET_API_URL = os.getenv("TARGET_API_URL", "https://api.github.com")

# Validate environment
for var_name, var_value in [
    ("GH_SOURCE_PAT", GH_SOURCE_PAT),
    ("GH_PAT", GH_PAT),
    ("SOURCE", SOURCE_ORG),
    ("DESTINATION", DESTINATION_ORG),
]:
    if not var_value:
        print(f"[ERROR] Environment variable {var_name} not set. Exiting.", flush=True)
        raise SystemExit(1)

# CSV file
CSV_FILE = os.path.join(os.getcwd(), "repos.csv")
if not os.path.exists(CSV_FILE):
    print(f"[ERROR] CSV file not found at {CSV_FILE}. Exiting.", flush=True)
    raise SystemExit(1)

# Folders & files
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = "MigrationDetails.csv"

# Error log (UTF-8)
logging.basicConfig(
    filename="migration_errors.log",
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

# gh CLI (set full path if not in PATH)
GH_CLI = "gh"  # e.g., r"C:\Program Files\GitHub CLI\gh.exe"

def safe_log_name(name: str) -> str:
    """Sanitize a string for use as a filename."""
    # Replace anything that's not alnum, dot, dash, or underscore
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)

def run_streaming(cmd, live_prefix="", log_path=None, env=None):
    """
    Run a shell command and stream stdout/stderr to console in real time.
    Also collects the full output and optionally writes to a log file.
    Returns (success: bool, combined_output: str)
    """
    # Prepare per-repo log file if requested
    log_file = None
    try:
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_file = open(log_path, "w", encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Could not open log file {log_path}: {e}", flush=True)

    # Merge stderr into stdout so we see everything
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,     # line-buffered
        shell=True,    # Fine from Git Bash / PowerShell; quoting is already added
        env=env
    )

    output_lines = []
    try:
        for line in proc.stdout:
            # Print each line as it arrives
            line = line.rstrip("\n")
            print(f"{live_prefix}{line}", flush=True)
            output_lines.append(line + "\n")
            if log_file:
                log_file.write(line + "\n")

        proc.wait()
        success = (proc.returncode == 0)
    except KeyboardInterrupt:
        # If user hits Ctrl+C, stop the child process as well
        try:
            proc.terminate()
        except Exception:
            pass
        success = False
        output_lines.append("\n[ABORTED] Interrupted by user.\n")
        if log_file:
            log_file.write("\n[ABORTED] Interrupted by user.\n")
    finally:
        if log_file:
            log_file.close()

    return success, "".join(output_lines)

def migrate_repos(csv_file):
    # Read CSV with UTF-8 (support BOM). Expect headers: CURRENT-NAME, NEW-NAME
    with open(csv_file, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        repo_list = [row for row in reader if (row.get("CURRENT-NAME") or "").strip()]

    total = len(repo_list)
    if total == 0:
        print("[INFO] No repositories found in CSV (CURRENT-NAME column). Nothing to do.", flush=True)
        return

    print(f"[INFO] Loaded {total} repo(s) from {csv_file}", flush=True)

    results = []
    completed = 0

    # Prepare environment for gh command (ensure PATs available to the process)
    # If you need different tokens for source/target, gh gei uses environment variables internally.
    # Keep current process env and pass through.
    env = os.environ.copy()

    for row in repo_list:
        source_repo = (row.get("CURRENT-NAME") or "").strip()
        target_repo = (row.get("NEW-NAME") or "").strip()

        # Fallback: if NEW-NAME blank, reuse CURRENT-NAME
        if not target_repo:
            target_repo = source_repo

        print(f"\nStarting migration: {SOURCE_ORG}/{source_repo} -> {DESTINATION_ORG}/{target_repo}", flush=True)

        cmd = (
            f'"{GH_CLI}" gei migrate-repo '
            f'--github-source-org "{SOURCE_ORG}" '
            f'--source-repo "{source_repo}" '
            f'--github-target-org "{DESTINATION_ORG}" '
            f'--target-repo "{target_repo}" '
            f'--target-api-url "{TARGET_API_URL}"'
        )

        start_time = datetime.now(timezone.utc)
        per_repo_log = LOGS_DIR / f"{safe_log_name(source_repo)}__to__{safe_log_name(target_repo)}.log"

        success, output = run_streaming(
            cmd,
            live_prefix=f"[{source_repo} -> {target_repo}] ",
            log_path=str(per_repo_log),
            env=env
        )
        end_time = datetime.now(timezone.utc)

        duration_seconds = (end_time - start_time).total_seconds()
        duration_minutes = round(duration_seconds / 60, 2)
        completed += 1

        status_msg = "Completed" if success else "Failed"
        print(
            f"[{status_msg}] {source_repo} -> {target_repo} "
            f"({completed}/{total}) in {round(duration_seconds, 2)}s",
            flush=True
        )

        if not success:
            logging.error("Migration failed for %s -> %s\n%s", source_repo, target_repo, output)

        results.append({
            "SourceOrg": SOURCE_ORG,
            "SourceRepo": source_repo,
            "TargetOrg": DESTINATION_ORG,
            "TargetRepo": target_repo,
            "Status": "Success" if success else "Failed",
            "StartTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "EndTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "TimeTakenSeconds": round(duration_seconds, 2),
            "TimeTakenMinutes": duration_minutes,
            "LogFile": str(per_repo_log)
        })

    # Write summary CSV (utf-8-sig so Excel opens cleanly)
    fieldnames = [
        "SourceOrg", "SourceRepo", "TargetOrg", "TargetRepo",
        "Status", "StartTime", "EndTime", "TimeTakenSeconds", "TimeTakenMinutes", "LogFile"
    ]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as csvout:
        writer = csv.DictWriter(csvout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nAll migrations finished. {completed}/{total} repos processed.", flush=True)
    print(f"Details -> {OUTPUT_FILE}", flush=True)
    print(f"Errors  -> migration_errors.log", flush=True)
    print(f"Per-repo logs -> {LOGS_DIR.resolve()}", flush=True)

if __name__ == "__main__":
    migrate_repos(CSV_FILE)
 
