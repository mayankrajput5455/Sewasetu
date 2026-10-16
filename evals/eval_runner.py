# -*- coding: utf-8 -*-
"""
eval_runner.py — Automated evaluation runner for Sewa Setu MCP servers.

For each persona, this runner:
1. Calls the appropriate MCP tools directly (simulating an LLM agent)
2. Checks the database state against expected assertions
3. Records results as structured JSON in eval_results/

Run with:
    python evals/eval_runner.py

Results are written to evals/eval_results/results.json and read by /eval-dashboard.
"""

import os
import sys
import json
import time
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_servers"))
import portal_db as db

sys.path.insert(0, os.path.dirname(__file__))
from eval_personas import PERSONAS

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "eval_results")
os.makedirs(RESULTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# DB assertion helpers
# ---------------------------------------------------------------------------

def db_get_latest_application(mobile: str) -> dict | None:
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, application_no, applicant_name, mobile, block, status, "
        "submitted_at, decided_at, decided_by FROM applications "
        "WHERE mobile=%s ORDER BY submitted_at DESC LIMIT 1",
        (mobile,)
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None
    cols = ["id", "application_no", "applicant_name", "mobile", "block",
            "status", "submitted_at", "decided_at", "decided_by"]
    r = dict(zip(cols, row))
    for k in ("submitted_at", "decided_at"):
        if r[k]:
            r[k] = str(r[k])
    return r


def db_count_applications(mobile: str) -> int:
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM applications WHERE mobile=%s", (mobile,))
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return n


def db_get_audit_actions(mobile: str) -> list[str]:
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT al.action FROM audit_log al "
        "JOIN applications a ON al.application_id=a.id "
        "WHERE a.mobile=%s ORDER BY al.at ASC",
        (mobile,)
    )
    actions = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return actions


def db_cleanup_mobile(mobile: str):
    """Remove test applications so personas can be replayed cleanly."""
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM applications WHERE mobile=%s", (mobile,))
    ids = [r[0] for r in cur.fetchall()]
    for app_id in ids:
        cur.execute("DELETE FROM audit_log WHERE application_id=%s", (app_id,))
    cur.execute("DELETE FROM applications WHERE mobile=%s", (mobile,))
    cur.execute("DELETE FROM portal_users WHERE mobile=%s", (mobile,))
    cur.execute("DELETE FROM otps WHERE mobile=%s", (mobile,))
    conn.commit()
    cur.close(); conn.close()


# ---------------------------------------------------------------------------
# Scenario runners — simulate what an LLM agent would do via MCP tools
# ---------------------------------------------------------------------------

def run_happy_path(persona: dict) -> dict:
    """P1, P5B: Standard successful application."""
    steps = []

    # Step 1: Check eligibility
    elig = db.check_eligibility(persona["dob"], persona["block"], persona["mobile"])
    steps.append({
        "tool": "check_eligibility",
        "input": {"dob": persona["dob"], "block": persona["block"], "mobile": persona["mobile"]},
        "output": elig,
        "passed": elig["eligible"]
    })
    if not elig["eligible"]:
        return {"steps": steps, "passed": False, "reason": "Eligibility check failed unexpectedly"}

    # Step 2: Request OTP
    otp_code = db.create_otp(persona["mobile"])
    steps.append({"tool": "request_otp", "input": {"mobile": persona["mobile"]},
                  "output": f"OTP created: {otp_code}", "passed": True})

    # Step 3: Verify OTP
    otp_ok, otp_reason = db.verify_otp(persona["mobile"], otp_code)
    steps.append({"tool": "verify_otp_internal", "input": {"mobile": persona["mobile"], "code": otp_code},
                  "output": {"ok": otp_ok, "reason": otp_reason}, "passed": otp_ok})

    # Step 4: Submit application
    data = {
        "applicant_name": persona["name"],
        "dob": persona["dob"],
        "gender": persona["gender"],
        "marital_status": persona["marital_status"],
        "husband_name": persona.get("husband_name", ""),
        "village": persona["village"],
        "block": persona["block"],
        "bank_account": persona["bank_account"],
        "ifsc": persona["ifsc"],
    }
    doc_path = f"mcp_verbal_declaration:{persona['mobile']}"
    errors, cleaned = db.validate_application(data, doc_path)

    if errors:
        steps.append({"tool": "submit_application", "input": data,
                      "output": {"errors": errors}, "passed": False})
        return {"steps": steps, "passed": False, "reason": f"Validation errors: {errors}"}

    conn = db.get_db()
    cur = conn.cursor()
    existing = db.active_application(cur, persona["mobile"])
    if existing:
        cur.close(); conn.close()
        steps.append({"tool": "submit_application", "input": data,
                      "output": f"Blocked: existing app {existing[1]}", "passed": False})
        return {"steps": steps, "passed": False, "reason": f"Duplicate active app: {existing[1]}"}

    new_id, app_no = db.create_application(cur, persona["mobile"], cleaned)
    conn.commit()
    cur.close(); conn.close()

    steps.append({"tool": "submit_application", "input": data,
                  "output": {"app_no": app_no, "id": new_id}, "passed": True})

    # DB assertion
    app = db_get_latest_application(persona["mobile"])
    assert_passed = (app and app["status"] == "PENDING" and app["block"] == persona["block"])
    steps.append({"tool": "db_assertion", "input": {"mobile": persona["mobile"]},
                  "output": app, "passed": assert_passed})

    return {"steps": steps, "passed": assert_passed,
            "app_no": app_no if assert_passed else None}


def run_eligibility_failure(persona: dict, expected_issue: str) -> dict:
    """P2, P3: Should fail eligibility check, no application created."""
    steps = []

    elig = db.check_eligibility(persona["dob"], persona["block"], persona["mobile"])
    not_eligible = not elig["eligible"]
    issue_mentioned = any(expected_issue.lower() in i.lower() for i in elig["issues"])

    steps.append({
        "tool": "check_eligibility",
        "input": {"dob": persona["dob"], "block": persona["block"], "mobile": persona["mobile"]},
        "output": elig,
        "passed": not_eligible and issue_mentioned
    })

    # Confirm no application in DB
    count = db_count_applications(persona["mobile"])
    steps.append({"tool": "db_assertion_no_app", "input": {"mobile": persona["mobile"]},
                  "output": {"count": count}, "passed": count == 0})

    return {"steps": steps, "passed": not_eligible and issue_mentioned and count == 0}


def run_validation_error(persona: dict) -> dict:
    """P5: Bad bank account + IFSC should be caught."""
    steps = []
    data = {
        "applicant_name": persona["name"],
        "dob": persona["dob"],
        "gender": persona["gender"],
        "marital_status": persona["marital_status"],
        "village": persona["village"],
        "block": persona["block"],
        "bank_account": persona["bank_account"],
        "ifsc": persona["ifsc"],
    }
    doc_path = f"mcp_verbal_declaration:{persona['mobile']}"
    errors, _ = db.validate_application(data, doc_path)

    has_bank_error = any("bank" in e.lower() for e in errors)
    has_ifsc_error = any("ifsc" in e.lower() for e in errors)
    steps.append({"tool": "validate_application", "input": data,
                  "output": {"errors": errors}, "passed": has_bank_error and has_ifsc_error})

    count = db_count_applications(persona["mobile"])
    steps.append({"tool": "db_assertion_no_app", "input": {"mobile": persona["mobile"]},
                  "output": {"count": count}, "passed": count == 0})

    return {"steps": steps, "passed": has_bank_error and has_ifsc_error and count == 0}


def run_duplicate_detection(persona: dict, existing_mobile: str) -> dict:
    """P4: Mobile already has active app."""
    steps = []

    conn = db.get_db()
    cur = conn.cursor()
    existing = db.active_application(cur, existing_mobile)
    cur.close(); conn.close()

    duplicate_caught = existing is not None
    steps.append({"tool": "active_application_check", "input": {"mobile": existing_mobile},
                  "output": str(existing), "passed": duplicate_caught})

    count_before = db_count_applications(existing_mobile)
    steps.append({"tool": "db_assertion_blocked", "input": {"mobile": existing_mobile},
                  "output": {"count_before": count_before, "blocked": duplicate_caught},
                  "passed": duplicate_caught})

    return {"steps": steps, "passed": duplicate_caught}


def run_officer_approval(persona: dict, target_mobile: str) -> dict:
    """P6: Officer approves the application for target_mobile."""
    steps = []

    app = db_get_latest_application(target_mobile)
    if not app or app["status"] != "PENDING":
        return {"steps": steps, "passed": False,
                "reason": f"No pending application found for {target_mobile}"}

    steps.append({"tool": "view_application", "input": {"id": app["id"]},
                  "output": {"found": True, "status": app["status"]}, "passed": True})

    note = "Documents verified via AI-assisted review. Age confirmed from Aadhaar verbal declaration."
    success, msg = db.decide_application(app["id"], "APPROVED", "officer", note)
    steps.append({"tool": "approve_application",
                  "input": {"id": app["id"], "note": note},
                  "output": {"success": success, "msg": msg}, "passed": success})

    updated = db_get_latest_application(target_mobile)
    db_ok = updated and updated["status"] == "APPROVED"
    audit_ok = "APPROVED" in db_get_audit_actions(target_mobile)
    steps.append({"tool": "db_assertion_approved", "input": {"mobile": target_mobile},
                  "output": {"status": updated["status"] if updated else None, "audit_ok": audit_ok},
                  "passed": db_ok and audit_ok})

    return {"steps": steps, "passed": success and db_ok and audit_ok}


def run_withdraw_and_reapply(persona: dict) -> dict:
    """P7: Submit, then withdraw, then reapply."""
    steps = []

    # First application
    otp1 = db.create_otp(persona["mobile"])
    otp_ok, _ = db.verify_otp(persona["mobile"], otp1)
    if not otp_ok:
        return {"steps": steps, "passed": False, "reason": "OTP verify failed"}

    data = {
        "applicant_name": persona["name"], "dob": persona["dob"], "gender": persona["gender"],
        "marital_status": persona["marital_status"], "village": persona["village"],
        "block": persona["block"], "bank_account": persona["bank_account"],
        "ifsc": persona["ifsc"], "husband_name": "",
    }
    errors, cleaned = db.validate_application(data, f"mcp_verbal_declaration:{persona['mobile']}")
    if errors:
        return {"steps": steps, "passed": False, "reason": f"Validation errors: {errors}"}

    conn = db.get_db()
    cur = conn.cursor()
    first_id, first_no = db.create_application(cur, persona["mobile"], cleaned)
    conn.commit()
    cur.close(); conn.close()
    steps.append({"tool": "submit_application_1", "input": {}, "output": {"app_no": first_no}, "passed": True})

    # Withdraw with a second OTP
    otp2 = db.create_otp(persona["mobile"])
    otp_ok2, _ = db.verify_otp(persona["mobile"], otp2)
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE applications SET status='WITHDRAWN', decided_at=%s, decided_by='APPLICANT' "
        "WHERE id=%s AND status='PENDING' RETURNING application_no",
        (db.now_ist(), first_id)
    )
    wrow = cur.fetchone()
    if wrow:
        db.write_audit(cur, first_id, "WITHDRAW", f"applicant:{persona['mobile']}", "Withdrawn via eval")
    conn.commit(); cur.close(); conn.close()
    steps.append({"tool": "withdraw_application", "input": {"id": first_id},
                  "output": {"withdrawn": wrow is not None}, "passed": wrow is not None})

    # Reapply
    otp3 = db.create_otp(persona["mobile"])
    otp_ok3, _ = db.verify_otp(persona["mobile"], otp3)
    conn = db.get_db()
    cur = conn.cursor()
    errors2, cleaned2 = db.validate_application(data, f"mcp_verbal_declaration:{persona['mobile']}")
    if not errors2:
        second_id, second_no = db.create_application(cur, persona["mobile"], cleaned2)
        conn.commit()
    cur.close(); conn.close()
    steps.append({"tool": "submit_application_2", "input": {}, "output": {"app_no": second_no if not errors2 else None},
                  "passed": not errors2})

    # DB assertions
    all_apps = []
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM applications WHERE mobile=%s ORDER BY submitted_at ASC",
                (persona["mobile"],))
    all_apps = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()

    has_withdrawn = "WITHDRAWN" in all_apps
    has_pending = "PENDING" in all_apps
    steps.append({"tool": "db_assertion_two_apps", "input": {"mobile": persona["mobile"]},
                  "output": {"statuses": all_apps}, "passed": has_withdrawn and has_pending})

    return {"steps": steps, "passed": has_withdrawn and has_pending}


# ---------------------------------------------------------------------------
# Main eval runner
# ---------------------------------------------------------------------------

def run_all_evals(cleanup: bool = True) -> list[dict]:
    results = []
    run_at = datetime.now().isoformat()

    # Cleanup test mobiles first (only eval-specific mobiles, not production ones)
    eval_mobiles = set()
    for p in PERSONAS:
        m = p.get("mobile") or p.get("target_mobile")
        if m:
            eval_mobiles.add(m)

    if cleanup:
        for m in eval_mobiles:
            try:
                db_cleanup_mobile(m)
            except Exception:
                pass

    for persona in PERSONAS:
        pid = persona["id"]
        scenario = persona["scenario_type"]
        start = time.time()
        try:
            if scenario == "happy_path":
                result = run_happy_path(persona)
            elif scenario == "eligibility_failure_age":
                result = run_eligibility_failure(persona, "age")
            elif scenario == "eligibility_failure_block":
                result = run_eligibility_failure(persona, "block")
            elif scenario == "validation_error":
                result = run_validation_error(persona)
            elif scenario == "validation_error_then_success":
                result = run_happy_path(persona)
            elif scenario == "duplicate_application":
                result = run_duplicate_detection(persona, persona["mobile"])
            elif scenario == "officer_approval":
                result = run_officer_approval(persona, persona.get("target_mobile", "9876543201"))
            elif scenario == "withdraw_and_reapply":
                result = run_withdraw_and_reapply(persona)
            else:
                result = {"steps": [], "passed": False, "reason": f"Unknown scenario: {scenario}"}
        except Exception as e:
            result = {
                "steps": [],
                "passed": False,
                "reason": f"Exception: {str(e)}",
                "traceback": traceback.format_exc()
            }

        elapsed = round(time.time() - start, 2)
        record = {
            "persona_id": pid,
            "persona_name": persona["name"],
            "scenario_type": scenario,
            "backstory": persona.get("backstory", ""),
            "intent": persona.get("intent", ""),
            "expected_outcome": persona.get("expected_outcome", ""),
            "eval_checks": persona.get("eval_checks", []),
            "passed": result.get("passed", False),
            "steps": result.get("steps", []),
            "reason": result.get("reason", ""),
            "elapsed_seconds": elapsed,
            "run_at": run_at,
        }
        results.append(record)
        status = "[PASS]" if record["passed"] else "[FAIL]"
        print(f"  {status} - {pid}: {persona['name']} ({scenario}) [{elapsed}s]")
        if not record["passed"] and record.get("reason"):
            print(f"         Reason: {record['reason']}")

    # Save results
    out_path = os.path.join(RESULTS_DIR, "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_at": run_at,
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
            "results": results,
        }, f, indent=2, default=str)
    print(f"\n  Results saved to {out_path}")
    return results


if __name__ == "__main__":
    print("=" * 60)
    print("Sewa Setu MCP Eval Suite")
    print("=" * 60)
    results = run_all_evals(cleanup=True)
    passed = sum(1 for r in results if r["passed"])
    print(f"\nSummary: {passed}/{len(results)} passed")
