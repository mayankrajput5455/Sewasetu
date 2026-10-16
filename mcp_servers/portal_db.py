# -*- coding: utf-8 -*-
"""
portal_db.py — thin data-access layer shared by both MCP servers.

All mutations go through the same validate_application / create_application
logic that the Flask portal uses, so every channel is subject to the same
business rules.  The MCP servers import this module; they never talk to the
database directly except through these functions.
"""

import os
import re
import hashlib
import configparser
import secrets
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import psycopg2

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# config – env vars take priority; fallback to app.ini when available
# ---------------------------------------------------------------------------

_BASE = os.path.join(os.path.dirname(__file__), "..", "app")
_config = configparser.ConfigParser()
_ini_path = os.path.join(_BASE, "config", "app.ini")
if os.path.exists(_ini_path):
    _config.read(_ini_path)


def _cfg(section, key, default=""):
    try:
        return _config.get(section, key)
    except Exception:
        return default


_DB = dict(
    host=os.environ.get("DB_HOST") or _cfg("database", "host", "db"),
    port=int(os.environ.get("DB_PORT") or _cfg("database", "port", "5432")),
    dbname=os.environ.get("DB_NAME") or _cfg("database", "name", "sewasetu"),
    user=os.environ.get("DB_USER") or _cfg("database", "user", "sewasetu"),
    password=os.environ.get("DB_PASSWORD") or _cfg("database", "password", "sewasetu123"),
)

SMS_GATEWAY_URL = os.environ.get("SMS_GATEWAY_URL") or _cfg("app", "sms_gateway_url", "http://smsgw:8025")
OTP_VALIDITY_SECONDS = int(os.environ.get("OTP_VALIDITY_SECONDS") or _cfg("app", "otp_validity_seconds", "300"))
SCHEME_DEADLINE = datetime.strptime(
    os.environ.get("SCHEME_DEADLINE") or _cfg("pension", "scheme_deadline", "2026-10-31 23:59"),
    "%Y-%m-%d %H:%M"
)
MIN_AGE = int(os.environ.get("MIN_AGE") or _cfg("pension", "min_age", "60"))
SLA_DAYS = int(os.environ.get("SLA_DAYS") or _cfg("pension", "sla_days", "15"))

BLOCKS = ["Sonari", "Rajapara", "Dhemaji Pathar", "Borgaon", "Namti", "Khelua"]
ACTIVE_STATUSES = ("PENDING", "APPROVED", "DEEMED_APPROVED")


def now_ist():
    return datetime.now(IST).replace(tzinfo=None)


def get_db():
    try:
        return psycopg2.connect(**_DB)
    except psycopg2.OperationalError:
        if _DB.get("host") == "db":
            fallback = dict(_DB, host="127.0.0.1")
            return psycopg2.connect(**fallback)
        raise


def hash_password(p: str) -> str:
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------

import requests as _http


def send_sms(mobile: str, text: str):
    gw_url = SMS_GATEWAY_URL
    if "smsgw" in gw_url and not os.environ.get("SMS_GATEWAY_URL"):
        gw_url = "http://127.0.0.1:8025"
    try:
        _http.post(gw_url + "/api/send", json={"to": mobile, "text": text}, timeout=1)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------

def create_otp(mobile: str) -> str:
    """Generate, store and send an OTP.  Returns the 6-digit code."""
    import random
    code = str(random.randint(100000, 999999))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO otps (mobile, code, created_at) VALUES (%s,%s,%s)",
                (mobile, code, now_ist()))
    conn.commit()
    cur.close(); conn.close()
    send_sms(mobile, f"Your Sewa Setu OTP is {code}. Valid for 5 minutes.")
    return code


def verify_otp(mobile: str, code: str) -> tuple[bool, str]:
    """
    Returns (ok, reason).
    reason is "" on success, otherwise a plain-language error.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT code, created_at FROM otps WHERE mobile = %s ORDER BY id DESC LIMIT 1",
                (mobile,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return False, "No OTP was requested for this mobile number."
    if row[0] != code:
        return False, "OTP is incorrect."
    age = (now_ist() - row[1]).total_seconds()
    if age > OTP_VALIDITY_SECONDS:
        return False, "OTP has expired.  Please request a new one."
    return True, ""


# ---------------------------------------------------------------------------
# Scheme rules — identical to the portal's validate_application()
# ---------------------------------------------------------------------------

def sanitize(value, maxlen=100):
    if value is None:
        return ""
    return str(value).strip()[:maxlen]


def parse_dob(raw: str):
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def validate_application(data: dict, doc_path: str):
    """
    Mirrors app/app.py:validate_application exactly.
    Returns (errors: list[str], cleaned: dict).
    """
    errors = []
    cleaned = {
        "applicant_name": sanitize(data.get("applicant_name")),
        "village": (data.get("village") or "").strip(),
        "block": (data.get("block") or "").strip(),
        "gender": data.get("gender") or "",
        "marital_status": data.get("marital_status") or "",
        "husband_name": (data.get("husband_name") or "").strip(),
        "husband_employer": "",
        "bank_account": re.sub(r"[\s-]", "", data.get("bank_account") or ""),
        "ifsc": re.sub(r"[\s-]", "", (data.get("ifsc") or "")).upper(),
        "doc_path": doc_path or "",
        "dob": None,
    }
    for label, key in (("full name", "applicant_name"), ("village", "village"),
                       ("block", "block"), ("bank account", "bank_account")):
        if not cleaned[key]:
            errors.append(f"{label} is required")
    if cleaned["block"] and cleaned["block"] not in BLOCKS:
        errors.append("block must be one of: " + ", ".join(BLOCKS))
    if cleaned["bank_account"] and not re.fullmatch(r"\d{9,18}", cleaned["bank_account"]):
        errors.append("bank account number must be 9 to 18 digits")
    if cleaned["ifsc"] and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", cleaned["ifsc"]):
        errors.append("IFSC must be 11 characters, e.g. SBIN0003077")
    dob = parse_dob(data.get("dob"))
    if dob is None:
        errors.append("date of birth must be given as DD/MM/YYYY")
    else:
        cleaned["dob"] = dob
        if age_on(dob, now_ist().date()) < MIN_AGE:
            errors.append(f"applicant must be {MIN_AGE} years of age or above")
    if not cleaned["doc_path"]:
        errors.append("age proof document is required")
    if now_ist() > SCHEME_DEADLINE:
        errors.append("the application window has closed")
    return errors, cleaned


def active_application(cur, mobile: str):
    cur.execute(
        "SELECT id, application_no, status FROM applications "
        "WHERE mobile = %s AND status IN %s ORDER BY submitted_at DESC LIMIT 1",
        (mobile, ACTIVE_STATUSES)
    )
    return cur.fetchone()


def write_audit(cur, app_id: int, action: str, actor: str, note: str = ""):
    cur.execute(
        "INSERT INTO audit_log (application_id, action, actor, note, at) VALUES (%s,%s,%s,%s,%s)",
        (app_id, action, actor, note, now_ist())
    )


def new_application_no() -> str:
    import random
    return "SSP" + datetime.now().strftime("%y") + str(random.randint(100000, 999999))


def create_application(cur, mobile: str, cleaned: dict):
    """
    Mirrors app/app.py:create_application exactly.
    Caller must commit.  Returns (id, application_no).
    """
    app_no = new_application_no()
    cur.execute(
        """INSERT INTO applications
           (application_no, applicant_name, mobile, dob, gender, marital_status,
            husband_name, husband_employer, village, block, bank_account, ifsc,
            doc_path, status, submitted_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
           RETURNING id""",
        (app_no, cleaned["applicant_name"], mobile, cleaned["dob"], cleaned["gender"],
         cleaned["marital_status"], cleaned["husband_name"], cleaned["husband_employer"],
         cleaned["village"], cleaned["block"], cleaned["bank_account"], cleaned["ifsc"],
         cleaned["doc_path"], now_ist())
    )
    new_id = cur.fetchone()[0]
    portal_pass = cleaned["dob"].strftime("%d%m%Y")
    cur.execute("SELECT count(*) FROM portal_users WHERE mobile = %s", (mobile,))
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES (%s,%s)",
                    (mobile, hash_password(portal_pass)))
    write_audit(cur, new_id, "SUBMIT", f"applicant:{mobile}")
    return new_id, app_no


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_application_by_mobile(mobile: str) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, application_no, applicant_name, mobile, dob, village, block, "
        "bank_account, ifsc, status, submitted_at, decided_at, decided_by "
        "FROM applications WHERE mobile = %s ORDER BY submitted_at DESC LIMIT 1",
        (mobile,)
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None
    cols = ["id", "application_no", "applicant_name", "mobile", "dob", "village", "block",
            "bank_account", "ifsc", "status", "submitted_at", "decided_at", "decided_by"]
    result = dict(zip(cols, row))
    for k in ("dob", "submitted_at", "decided_at"):
        if result[k]:
            result[k] = str(result[k])
    return result


def get_application_by_id(app_id: int) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, application_no, applicant_name, mobile, dob, gender, marital_status, "
        "village, block, bank_account, ifsc, doc_path, status, submitted_at, decided_at, decided_by "
        "FROM applications WHERE id = %s",
        (app_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return None
    cols = ["id", "application_no", "applicant_name", "mobile", "dob", "gender", "marital_status",
            "village", "block", "bank_account", "ifsc", "doc_path", "status", "submitted_at",
            "decided_at", "decided_by"]
    result = dict(zip(cols, row))
    for k in ("dob", "submitted_at", "decided_at"):
        if result[k]:
            result[k] = str(result[k])
    cur.execute(
        "SELECT at, action, actor, note FROM audit_log WHERE application_id = %s ORDER BY at ASC",
        (app_id,)
    )
    result["audit"] = [{"at": str(r[0]), "action": r[1], "actor": r[2], "note": r[3]}
                       for r in cur.fetchall()]
    cur.close(); conn.close()
    return result


def list_pending_queue(block: str | None = None, limit: int = 50) -> list[dict]:
    conn = get_db()
    cur = conn.cursor()
    now = now_ist()
    sla_cutoff = now - timedelta(days=SLA_DAYS)
    if block:
        cur.execute(
            "SELECT id, application_no, applicant_name, mobile, village, block, "
            "submitted_at, (submitted_at < %s) AS overdue "
            "FROM applications WHERE status='PENDING' AND block=%s "
            "ORDER BY submitted_at ASC LIMIT %s",
            (sla_cutoff, block, limit)
        )
    else:
        cur.execute(
            "SELECT id, application_no, applicant_name, mobile, village, block, "
            "submitted_at, (submitted_at < %s) AS overdue "
            "FROM applications WHERE status='PENDING' "
            "ORDER BY submitted_at ASC LIMIT %s",
            (sla_cutoff, limit)
        )
    cols = ["id", "application_no", "applicant_name", "mobile", "village", "block",
            "submitted_at", "overdue"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if r["submitted_at"]:
            r["submitted_at"] = str(r["submitted_at"])
            days_pending = (now - datetime.fromisoformat(r["submitted_at"])).days
            r["days_pending"] = days_pending
            r["days_until_deemed"] = max(0, SLA_DAYS - days_pending)
    cur.close(); conn.close()
    return rows


def decide_application(app_id: int, decision: str, actor: str, note: str) -> tuple[bool, str]:
    """
    Records an APPROVED or REJECTED decision.
    Returns (success: bool, message: str).
    """
    decision = decision.upper()
    if decision not in ("APPROVED", "REJECTED"):
        return False, "decision must be APPROVED or REJECTED"
    if not note or not note.strip():
        return False, "a note/reason is required for all decisions"
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE applications SET status=%s, decided_at=%s, decided_by=%s "
        "WHERE id=%s AND status='PENDING' RETURNING application_no, mobile",
        (decision, now_ist(), actor, app_id)
    )
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return False, "Application not found or is not in PENDING state."
    write_audit(cur, app_id, decision, f"officer:{actor}", note)
    conn.commit()
    # notify citizen
    send_sms(row[1], f"Sewa Setu: your application {row[0]} has been {decision}.")
    cur.close(); conn.close()
    return True, f"Application {row[0]} has been {decision}."


def check_eligibility(dob_str: str, block: str, mobile: str) -> dict:
    """
    Pre-flight eligibility check before a citizen starts the full application.
    Returns {eligible: bool, issues: list[str], notes: list[str]}.
    """
    issues = []
    notes = []
    dob = parse_dob(dob_str)
    if dob is None:
        issues.append("Date of birth must be in DD/MM/YYYY format.")
    else:
        age = age_on(dob, now_ist().date())
        if age < MIN_AGE:
            issues.append(f"Applicant is {age} years old. Minimum age is {MIN_AGE}.")
        else:
            notes.append(f"Age {age} — eligible.")
    if block not in BLOCKS:
        issues.append(f"Block '{block}' is not covered. Eligible blocks: {', '.join(BLOCKS)}.")
    else:
        notes.append(f"Block {block} is covered.")
    if now_ist() > SCHEME_DEADLINE:
        issues.append("The application window has closed.")
    # check if mobile already has an active application
    conn = get_db()
    cur = conn.cursor()
    existing = active_application(cur, mobile)
    cur.close(); conn.close()
    if existing:
        issues.append(
            f"Mobile {mobile} already has an active application "
            f"({existing[1]}, status: {existing[2]}). "
            "A PENDING application can be withdrawn to apply afresh."
        )
    return {"eligible": len(issues) == 0, "issues": issues, "notes": notes}
