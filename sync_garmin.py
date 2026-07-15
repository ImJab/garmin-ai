"""
Pulls recent activities and daily wellness data from Garmin Connect and saves
them as plain-English markdown notes (plus a combined data.json) in the
garmin/ folder.

Login session tokens are cached in tokens/ so you only have to log in once.
This script never writes anything back to your Garmin account - it only reads.

Usage:
    python sync_garmin.py            # sync last 3 days (default)
    python sync_garmin.py --days 7   # sync last 7 days
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

import garminconnect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKENS_DIR = os.path.join(BASE_DIR, "tokens")
TOKEN_FILE = os.path.join(TOKENS_DIR, "garmin_tokens.json")
GARMIN_DIR = os.path.join(BASE_DIR, "garmin")
DATA_JSON_PATH = os.path.join(GARMIN_DIR, "data.json")

# Used only during the one-time interactive login. The creds file is deleted
# the instant it's read; the mfa file is deleted the instant it's read.
CREDS_FILE = os.path.join(BASE_DIR, "tokens", "_login_creds.json")
MFA_FILE = os.path.join(BASE_DIR, "tokens", "_mfa_code.txt")
MFA_WAIT_TIMEOUT_SEC = 15 * 60


def _credential_login():
    """One-time login using email/password dropped in CREDS_FILE by the caller."""
    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)
    email = creds["email"]
    password = creds["password"]
    # Remove the credentials file immediately - it should never linger on disk.
    os.remove(CREDS_FILE)

    api = garminconnect.Garmin(email=email, password=password, return_on_mfa=True)
    mfa_status, _ = api.login()

    if mfa_status == "needs_mfa":
        print("NEEDS_MFA: Garmin sent a verification code. Waiting for it...", flush=True)
        code = _wait_for_mfa_code()
        api.resume_login(None, code)
    else:
        # return_on_mfa short-circuits profile loading on the non-MFA path too.
        api._load_profile_and_settings()

    os.makedirs(TOKENS_DIR, exist_ok=True)
    api.client.dump(TOKENS_DIR)
    print("LOGIN_SUCCESS: session token saved.", flush=True)
    return api


def _wait_for_mfa_code():
    waited = 0
    while not os.path.exists(MFA_FILE):
        time.sleep(2)
        waited += 2
        if waited >= MFA_WAIT_TIMEOUT_SEC:
            raise TimeoutError("Timed out waiting for the 2FA code.")
    with open(MFA_FILE, "r", encoding="utf-8") as f:
        code = f.read().strip()
    os.remove(MFA_FILE)
    return code


def login():
    """Log in to Garmin Connect, reusing a saved session token if available."""
    if os.path.exists(TOKEN_FILE):
        try:
            api = garminconnect.Garmin()
            api.login(TOKENS_DIR)
            print("Logged in using saved session token.")
            return api
        except Exception as e:
            print(f"Saved session token didn't work ({e}); logging in fresh.")

    if not os.path.exists(CREDS_FILE):
        print("LOGIN_REQUIRED: no saved session and no credentials file found.", flush=True)
        raise SystemExit(2)

    return _credential_login()


def safe_get(fn, *args, retries=3, **kwargs):
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  (rate limited fetching {fn.__name__}, waiting {wait}s...)")
                time.sleep(wait)
                continue
            print(f"  (warning: could not fetch {fn.__name__}: {e})")
            return None


def fmt_minutes(seconds):
    if not seconds:
        return "0 min"
    return f"{round(seconds / 60)} min"


def write_wellness_note(day_str, wellness, all_data):
    sleep = wellness.get("sleep")
    hrv = wellness.get("hrv")
    rhr = wellness.get("rhr")
    body_battery = wellness.get("body_battery")
    stress = wellness.get("stress")
    steps = wellness.get("steps")
    readiness = wellness.get("readiness")

    lines = [f"# Wellness — {day_str}", ""]

    # Sleep
    lines.append("## Sleep")
    if sleep and sleep.get("dailySleepDTO"):
        s = sleep["dailySleepDTO"]
        total_sec = s.get("sleepTimeSeconds")
        deep = s.get("deepSleepSeconds")
        light = s.get("lightSleepSeconds")
        rem = s.get("remSleepSeconds")
        awake = s.get("awakeSleepSeconds")
        score = (s.get("sleepScores") or {}).get("overall", {}).get("value")
        lines.append(f"- Total sleep: {fmt_minutes(total_sec)}")
        if score is not None:
            lines.append(f"- Sleep score: {score}")
        lines.append(f"- Deep: {fmt_minutes(deep)}, Light: {fmt_minutes(light)}, REM: {fmt_minutes(rem)}, Awake: {fmt_minutes(awake)}")
    else:
        lines.append("- No sleep data available for this day.")
    lines.append("")

    # HRV
    lines.append("## Heart Rate Variability (HRV)")
    if hrv and hrv.get("hrvSummary"):
        h = hrv["hrvSummary"]
        lines.append(f"- Last night average: {h.get('lastNightAvg', 'n/a')} ms")
        lines.append(f"- 7-day average: {h.get('weeklyAvg', 'n/a')} ms")
        lines.append(f"- Status: {h.get('status', 'n/a')}")
    else:
        lines.append("- No HRV data available for this day.")
    lines.append("")

    # Resting heart rate
    lines.append("## Resting Heart Rate")
    if rhr:
        try:
            rhr_val = rhr["allMetrics"]["metricsMap"]["WELLNESS_RESTING_HEART_RATE"][0]["value"]
            lines.append(f"- Resting HR: {rhr_val} bpm")
        except (KeyError, IndexError, TypeError):
            lines.append("- No resting heart rate data available for this day.")
    else:
        lines.append("- No resting heart rate data available for this day.")
    lines.append("")

    # Body Battery
    lines.append("## Body Battery")
    if body_battery and isinstance(body_battery, list) and len(body_battery) > 0:
        bb = body_battery[0]
        charged = bb.get("charged")
        drained = bb.get("drained")
        lines.append(f"- Charged: {charged}, Drained: {drained}")
    else:
        lines.append("- No Body Battery data available for this day.")
    lines.append("")

    # Stress
    lines.append("## Stress")
    if stress:
        avg = stress.get("avgStressLevel")
        max_s = stress.get("maxStressLevel")
        lines.append(f"- Average stress level: {avg}")
        lines.append(f"- Max stress level: {max_s}")
    else:
        lines.append("- No stress data available for this day.")
    lines.append("")

    # Steps
    lines.append("## Steps")
    if steps and isinstance(steps, list):
        total_steps = sum(s.get("steps") or 0 for s in steps)
        lines.append(f"- Total steps: {total_steps}")
    else:
        lines.append("- No step data available for this day.")
    lines.append("")

    # Training readiness
    lines.append("## Training Readiness")
    if readiness and isinstance(readiness, list) and len(readiness) > 0:
        r = readiness[0]
        lines.append(f"- Score: {r.get('score', 'n/a')} ({r.get('level', 'n/a')})")
        feedback = r.get("feedbackLong") or r.get("feedbackShort")
        if feedback:
            lines.append(f"- Feedback: {feedback}")
    else:
        lines.append("- No training readiness data available for this day.")
    lines.append("")

    path = os.path.join(GARMIN_DIR, f"wellness-{day_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  wrote {os.path.basename(path)}")

    all_data.setdefault("wellness", {})[day_str] = wellness


def write_activity_note(activity, all_data):
    activity_id = activity.get("activityId")
    name = activity.get("activityName", "Untitled activity")
    activity_type = (activity.get("activityType") or {}).get("typeKey", "unknown")
    start_time = activity.get("startTimeLocal", "unknown time")
    day_str = start_time.split(" ")[0] if start_time != "unknown time" else "unknown-date"

    duration_sec = activity.get("duration")
    distance_m = activity.get("distance")
    avg_hr = activity.get("averageHR")
    max_hr = activity.get("maxHR")
    calories = activity.get("calories")
    avg_pace = activity.get("averageSpeed")
    training_effect = activity.get("aerobicTrainingEffect")
    anaerobic_effect = activity.get("anaerobicTrainingEffect")

    lines = [f"# {name}", "", f"- Date: {start_time}", f"- Type: {activity_type}"]

    if duration_sec:
        lines.append(f"- Duration: {fmt_minutes(duration_sec)}")
    if distance_m:
        lines.append(f"- Distance: {distance_m / 1000:.2f} km")
    if avg_hr:
        lines.append(f"- Average heart rate: {avg_hr} bpm")
    if max_hr:
        lines.append(f"- Max heart rate: {max_hr} bpm")
    if calories:
        lines.append(f"- Calories: {calories}")
    if avg_pace:
        lines.append(f"- Average speed: {avg_pace:.2f} m/s")
    if training_effect:
        lines.append(f"- Aerobic training effect: {round(training_effect, 1)}")
    if anaerobic_effect:
        lines.append(f"- Anaerobic training effect: {round(anaerobic_effect, 1)}")
    lines.append("")

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip().replace(" ", "_")
    filename = f"activity-{day_str}-{activity_id}-{safe_name}.md"
    path = os.path.join(GARMIN_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  wrote {os.path.basename(path)}")

    all_data.setdefault("activities", []).append(activity)


def sync(days):
    os.makedirs(GARMIN_DIR, exist_ok=True)
    api = login()

    all_data = {}
    if os.path.exists(DATA_JSON_PATH):
        try:
            with open(DATA_JSON_PATH, "r", encoding="utf-8") as f:
                all_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            all_data = {}

    today = date.today()

    print(f"\nFetching activities from the last {days} day(s)...")
    cutoff = today - timedelta(days=days)
    recent_activities = []
    page_size = 100
    start = 0
    for _page in range(200):  # hard cap: 20,000 activities
        batch = safe_get(api.get_activities, start, page_size) or []
        if not batch:
            break
        reached_cutoff = False
        for a in batch:
            start_time = a.get("startTimeLocal", "")
            try:
                a_date = date.fromisoformat(start_time.split(" ")[0])
            except ValueError:
                continue
            if a_date >= cutoff:
                recent_activities.append(a)
            else:
                # get_activities returns newest-first, so once we're past the
                # cutoff every remaining activity (this page and beyond) is too.
                reached_cutoff = True
                break
        if reached_cutoff or len(batch) < page_size:
            break
        start += page_size
        time.sleep(0.3)

    print(f"Found {len(recent_activities)} activities in range.")
    for a in recent_activities:
        write_activity_note(a, all_data)

    print(f"\nFetching wellness data for the last {days} day(s)...")
    for i in range(days):
        d = today - timedelta(days=i)
        day_str = d.isoformat()
        print(f"  {day_str}:")
        wellness = {
            "sleep": safe_get(api.get_sleep_data, day_str),
            "hrv": safe_get(api.get_hrv_data, day_str),
            "rhr": safe_get(api.get_rhr_day, day_str),
            "body_battery": safe_get(api.get_body_battery, day_str, day_str),
            "stress": safe_get(api.get_stress_data, day_str),
            "steps": safe_get(api.get_steps_data, day_str),
            "readiness": safe_get(api.get_training_readiness, day_str),
        }
        write_wellness_note(day_str, wellness, all_data)
        if i % 10 == 9:
            # Periodically flush progress to disk so a long backfill isn't
            # all-or-nothing if it gets interrupted partway through.
            with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(all_data, f, indent=2, default=str)
        time.sleep(0.2)

    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, default=str)
    print(f"\nSaved combined data to {DATA_JSON_PATH}")
    print(f"Notes saved in {GARMIN_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Garmin Connect data to local markdown notes.")
    parser.add_argument("--days", type=int, default=3, help="Number of past days to sync (default: 3)")
    args = parser.parse_args()

    try:
        sync(args.days)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
