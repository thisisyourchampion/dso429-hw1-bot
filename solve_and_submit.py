#!/usr/bin/env python3
"""
DSO 429 HW1 auto-submit bot.

Stateless by design: every run re-derives what still needs to be submitted by
diffing the published questions CSV against the student's own receipt
(rounds_submitted). See docs/superpowers/specs/2026-09-01-hw1-autosubmit-bot-design.md
for the reasoning.
"""
import csv
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import requests

CSV_URL = "https://raw.githubusercontent.com/FaisalXL/dso429-hw1-questions/main/questions.csv"
FORM_VIEW_URL = "https://docs.google.com/forms/d/e/1FAIpQLSc0fSvaiv3kHXv9uU-N4kaFc0K4H2NBavBFgZj4tG8WwM1GnA/viewform"
FORM_RESPONSE_URL = "https://docs.google.com/forms/d/e/1FAIpQLSc0fSvaiv3kHXv9uU-N4kaFc0K4H2NBavBFgZj4tG8WwM1GnA/formResponse"
RECEIPT_URL = "https://dso429-hw1-tick.dso429hw1.workers.dev/receipt"
USER_AGENT = "Mozilla/5.0 (compatible; DSO429-HW1-Bot/1.0; +https://github.com/)"

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


def fetch_receipt(email):
    r = requests.get(RECEIPT_URL, headers=HEADERS, params={"email": email}, timeout=30)
    r.raise_for_status()
    return r.json()


def get_form_entry_ids():
    """Re-fetch the live form and parse its current entry.<id> field mapping.
    Done fresh every call since we can't prove IDs are stable across rounds."""
    r = requests.get(FORM_VIEW_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*\])\s*;", r.text, re.DOTALL)
    if not m:
        raise RuntimeError("could not find FB_PUBLIC_LOAD_DATA_ in form HTML")
    data = json.loads(m.group(1))
    fields = data[1][1]

    entries = {}
    q_num = 0
    for f in fields:
        title = f[1] or ""
        entry_id = f[4][0][0]
        if title.strip().lower() == "email":
            entries["email"] = entry_id
        elif title.strip().lower() == "round_id":
            entries["round_id"] = entry_id
        elif re.match(r"^Q\d", title.strip()):
            q_num += 1
            entries[f"q{q_num}"] = entry_id

    required = {"email", "round_id", "q1", "q2", "q3", "q4", "q5"}
    missing = required - entries.keys()
    if missing:
        raise RuntimeError(f"form is missing expected fields: {missing}")
    return entries


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


def submit_round(round_id, email, answers):
    entries = get_form_entry_ids()
    payload = {
        f"entry.{entries['email']}": email,
        f"entry.{entries['round_id']}": str(round_id),
    }
    for i in range(1, 6):
        payload[f"entry.{entries[f'q{i}']}"] = answers[f"q{i}"]

    r = requests.post(FORM_RESPONSE_URL, headers=HEADERS, data=payload, timeout=30)
    r.raise_for_status()
    return r.status_code


def main():
    email = os.environ["STUDENT_EMAIL"]
    api_key = os.environ["GEMINI_API_KEY"]

    rounds = fetch_csv()
    receipt = fetch_receipt(email)
    if not receipt.get("registered"):
        log(f"WARNING: receipt says this email is not registered: {receipt.get('warnings')}")

    already = set(str(x) for x in receipt.get("rounds_submitted", []))
    missing = sorted((rid for rid in rounds if rid not in already), key=int)

    if not missing:
        log(f"nothing to do — {len(already)} rounds already recorded, CSV has {len(rounds)}")
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

        submit_round(rid, email, answers)

        landed = False
        for _ in range(4):
            time.sleep(5)
            check = fetch_receipt(email)
            landed = str(rid) in set(str(x) for x in check.get("rounds_submitted", []))
            if landed:
                break
        if landed:
            log(f"round {rid}: CONFIRMED via receipt")
        else:
            log(f"round {rid}: NOT CONFIRMED after waiting — {check.get('latest')}")


if __name__ == "__main__":
    main()
