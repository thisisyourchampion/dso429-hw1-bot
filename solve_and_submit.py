#!/usr/bin/env python3
"""
DSO 429 HW1 auto-submit bot.

Submission model (v2): answers are recorded by pushing a JSON file to this
same public repo at answers/round-NNNN.json - course staff poll the repo and
grade by first-seen commit, not by anything we claim locally. There is no
receipt endpoint in this model: the only source of truth for "have I already
answered round N" is whether answers/round-NNNN.json already exists in the
checked-out repo, and a file is NEVER touched again once written (editing
after being seen is ignored by staff, so overwriting would be pointless and
risks looking like tampering).

See docs/superpowers/specs/2026-09-01-hw1-autosubmit-bot-design.md and
docs/superpowers/specs/2026-09-02-submission-model-v2-design.md.
"""
import csv
import io
import json
import os
import random
import re
import subprocess
import time
from datetime import datetime, timezone

import requests

CSV_URL = "https://raw.githubusercontent.com/FaisalXL/dso429-hw1-questions/main/questions.csv"
USER_AGENT = "Mozilla/5.0 (compatible; DSO429-HW1-Bot/1.0; +https://github.com/)"
ANSWERS_DIR = "answers"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")


def gemini_url(model):
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

HEADERS = {"User-Agent": USER_AGENT}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fetch_csv():
    r = requests.get(CSV_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    rounds = {}
    for row in reader:
        rid = row["round_id"]
        rounds.setdefault(rid, []).append(row)
    for rid in rounds:
        rounds[rid].sort(key=lambda row: int(row["q_index"]))
    return rounds


def round_filename(rid):
    return f"{ANSWERS_DIR}/round-{int(rid):04d}.json"


def existing_rounds():
    """Round ids we've already written a file for, per the local checkout."""
    if not os.path.isdir(ANSWERS_DIR):
        return set()
    found = set()
    for name in os.listdir(ANSWERS_DIR):
        m = re.match(r"^round-(\d{4})\.json$", name)
        if m:
            found.add(str(int(m.group(1))))
    return found


def ask_gemini(questions, api_key, model=GEMINI_MODEL):
    """questions: list of 5 dicts (q_index order) with question_text/option_a..d.
    Returns dict {"q1": "A", ..., "q5": "D"} or raises on failure."""
    lines = []
    for q in questions:
        idx = q["q_index"]
        lines.append(
            f"Q{idx}. {q['question_text']}\n"
            f"A) {q['option_a']}\n"
            f"B) {q['option_b']}\n"
            f"C) {q['option_c']}\n"
            f"D) {q['option_d']}"
        )
    prompt = (
        "You are answering 5 independent multiple-choice business-math questions "
        "(break-even points, weighted averages, margins, discounts, compound growth, "
        "dates, ratios, probability, etc). For each question, work through the "
        "arithmetic carefully and precisely step by step, then pick exactly one of "
        "A/B/C/D. Respond with ONLY a JSON object mapping q1..q5 to a single letter, "
        'e.g. {"q1":"A","q2":"C","q3":"B","q4":"D","q5":"A"}. No other text.\n\n'
        + "\n\n".join(lines)
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 2048},
        },
    }
    r = requests.post(
        gemini_url(model),
        params={"key": api_key},
        json=body,
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    answers = json.loads(text)

    result = {}
    for i in range(1, 6):
        key = f"q{i}"
        val = str(answers.get(key, "")).strip().upper()
        if val not in ("A", "B", "C", "D"):
            raise ValueError(f"bad answer for {key}: {val!r}")
        result[key] = val
    return result


def answer_round(questions, api_key):
    """Try the primary model twice, then a secondary model once, then fall
    back to a random guess per-question so a round is never skipped outright
    (a skip costs both uptime and accuracy)."""
    attempts = [GEMINI_MODEL, GEMINI_MODEL, GEMINI_FALLBACK_MODEL]
    for i, model in enumerate(attempts, start=1):
        try:
            return ask_gemini(questions, api_key, model=model), f"gemini:{model}"
        except Exception as e:
            log(f"  attempt {i} ({model}) failed: {e}")
            if i < len(attempts):
                time.sleep(5)
    log("  falling back to random guesses for this round")
    return {f"q{i}": random.choice("ABCD") for i in range(1, 6)}, "fallback"


def run_git(*args, check=True):
    return subprocess.run(
        ["git", *args], check=check, capture_output=True, text=True
    )


def push_round_file(rid, email, answers):
    """Write answers/round-NNNN.json and push it as its own commit. Pushes as
    soon as the file is written (not batched) since grading is by first-seen
    push time, not commit time. Retries a couple of times against a fetch+
    rebase if the push is rejected (e.g. an overlapping run)."""
    os.makedirs(ANSWERS_DIR, exist_ok=True)
    path = round_filename(rid)
    with open(path, "w") as f:
        json.dump({"round_id": int(rid), "email": email, "answers": answers}, f, indent=2)
        f.write("\n")

    run_git("add", path)
    run_git("commit", "-m", f"answers for round {int(rid):04d}")

    for attempt in range(3):
        r = run_git("push", "origin", "HEAD:main", check=False)
        if r.returncode == 0:
            return True
        log(f"  push attempt {attempt + 1} failed: {r.stderr.strip()[:300]}")
        run_git("fetch", "origin", "main")
        rebase = run_git("rebase", "origin/main", check=False)
        if rebase.returncode != 0:
            run_git("rebase", "--abort", check=False)
            log("  rebase failed, aborting retries for this round")
            return False
    return False


def main():
    email = os.environ["STUDENT_EMAIL"]
    api_key = os.environ["GEMINI_API_KEY"]

    rounds = fetch_csv()
    have = existing_rounds()
    missing = sorted((rid for rid in rounds if rid not in have), key=int)

    if not missing:
        log(f"nothing to do — {len(have)} rounds already pushed, CSV has {len(rounds)}")
        return

    log(f"missing rounds: {missing}")
    for rid in missing:
        questions = rounds[rid]
        deadline = parse_iso(questions[0]["deadline_at"])
        now = datetime.now(timezone.utc)
        status = "OPEN" if now < deadline else "LATE"
        log(f"round {rid}: {status} (deadline {deadline.isoformat()})")

        answers, source = answer_round(questions, api_key)
        log(f"round {rid}: answers={answers} source={source}")

        pushed = push_round_file(rid, email, answers)
        if pushed:
            log(f"round {rid}: PUSHED")
        else:
            log(f"round {rid}: PUSH FAILED — file stays local, next run's fresh checkout will retry it")


if __name__ == "__main__":
    main()
