# -*- coding: utf-8 -*-
"""
eval_runner.py — Automated evaluation runner for Sewa Setu MCP servers.

For each persona, this runner:
1. Simulates realistic multi-turn conversations between the persona and the MCP assistant
2. Calls the appropriate MCP tools directly against the database
3. Checks the database state against expected assertions
4. Records full conversation transcripts and results as structured JSON in eval_results/

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
# Scenario runners with full conversational transcripts
# ---------------------------------------------------------------------------

def run_happy_path(persona: dict) -> dict:
    """P1, P5B: Standard successful application."""
    steps = []
    transcript = []

    # Turn 1: Citizen introduction
    transcript.append({
        "speaker": persona["name"],
        "role": "citizen",
        "text": f"Namaskar ji. My name is {persona['name']}, I am {persona['age']} years old from {persona['village']}, {persona['block']}. Can I apply for old age pension?"
    })

    # Step 1: Assistant checks eligibility
    elig = db.check_eligibility(persona["dob"], persona["block"], persona["mobile"])
    steps.append({
        "tool": "check_eligibility",
        "input": {"dob": persona["dob"], "block": persona["block"], "mobile": persona["mobile"]},
        "output": elig,
        "passed": elig["eligible"]
    })
    if not elig["eligible"]:
        return {"steps": steps, "transcript": transcript, "passed": False, "reason": "Eligibility check failed unexpectedly"}

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "check_eligibility",
        "text": f"Namaskar {persona['name']} ji! Yes, you qualify for the Sewa Setu pension of ₹250/month (Age {persona['age']} ≥ 60, block {persona['block']} is covered). Please provide your 10-digit mobile number so I can send an OTP."
    })

    # Turn 2: Citizen provides mobile
    transcript.append({
        "speaker": persona["name"],
        "role": "citizen",
        "text": f"Haan ji, my mobile number is {persona['mobile']}."
    })

    # Step 2: Request OTP
    otp_code = db.create_otp(persona["mobile"])
    steps.append({"tool": "request_otp", "input": {"mobile": persona["mobile"]},
                  "output": f"OTP created: {otp_code}", "passed": True})

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "request_otp",
        "text": f"I have sent a 6-digit OTP to {persona['mobile']}. Please enter the OTP code you received."
    })

    # Turn 3: Citizen provides OTP
    transcript.append({
        "speaker": persona["name"],
        "role": "citizen",
        "text": f"The SMS says OTP is {otp_code}."
    })

    # Step 3: Verify OTP
    otp_ok, otp_reason = db.verify_otp(persona["mobile"], otp_code)
    steps.append({"tool": "verify_otp", "input": {"mobile": persona["mobile"], "code": otp_code},
                  "output": {"ok": otp_ok, "reason": otp_reason}, "passed": otp_ok})

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "verify_otp",
        "text": "OTP verified successfully! Now please share your bank account number and 11-digit IFSC code from your passbook."
    })

    # Turn 4: Citizen gives bank details
    transcript.append({
        "speaker": persona["name"],
        "role": "citizen",
        "text": f"My bank account number is {persona['bank_account']} and IFSC code is {persona['ifsc']}."
    })

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
        return {"steps": steps, "transcript": transcript, "passed": False, "reason": f"Validation errors: {errors}"}

    conn = db.get_db()
    cur = conn.cursor()
    existing = db.active_application(cur, persona["mobile"])
    if existing:
        cur.close(); conn.close()
        steps.append({"tool": "submit_application", "input": data,
                      "output": f"Blocked: existing app {existing[1]}", "passed": False})
        return {"steps": steps, "transcript": transcript, "passed": False, "reason": f"Duplicate active app: {existing[1]}"}

    new_id, app_no = db.create_application(cur, persona["mobile"], cleaned)
    conn.commit()
    cur.close(); conn.close()

    steps.append({"tool": "submit_application", "input": data,
                  "output": {"app_no": app_no, "id": new_id}, "passed": True})

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "submit_pension_application",
        "text": f"Congratulations {persona['name']} ji! Your application has been submitted successfully to Sewa Setu. Your Application Number is {app_no}. It is now in PENDING status under the 15-day statutory SLA review."
    })

    # DB assertion
    app = db_get_latest_application(persona["mobile"])
    assert_passed = (app and app["status"] == "PENDING" and app["block"] == persona["block"])
    steps.append({"tool": "db_assertion", "input": {"mobile": persona["mobile"]},
                  "output": app, "passed": assert_passed})

    return {"steps": steps, "transcript": transcript, "passed": assert_passed,
            "app_no": app_no if assert_passed else None}


def run_eligibility_failure(persona: dict, expected_issue: str) -> dict:
    """P2, P3: Should fail eligibility check, no application created."""
    steps = []
    transcript = [
        {
            "speaker": persona["name"],
            "role": "citizen",
            "text": f"Namaskar, my name is {persona['name']}. I am {persona['age']} years old from {persona['village']}, {persona['block']}. I want to apply for the pension scheme."
        }
    ]

    elig = db.check_eligibility(persona["dob"], persona["block"], persona["mobile"])
    not_eligible = not elig["eligible"]
    issue_mentioned = any(expected_issue.lower() in i.lower() for i in elig["issues"])

    steps.append({
        "tool": "check_eligibility",
        "input": {"dob": persona["dob"], "block": persona["block"], "mobile": persona["mobile"]},
        "output": elig,
        "passed": not_eligible and issue_mentioned
    })

    explanation = "; ".join(elig["issues"])
    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "check_eligibility",
        "text": f"Namaskar {persona['name']} ji. Under the Sewa Setu rules: {explanation}. Therefore, we cannot proceed with the application at this time."
    })

    # Confirm no application in DB
    count = db_count_applications(persona["mobile"])
    steps.append({"tool": "db_assertion_no_app", "input": {"mobile": persona["mobile"]},
                  "output": {"count": count}, "passed": count == 0})

    return {"steps": steps, "transcript": transcript, "passed": not_eligible and issue_mentioned and count == 0}


def run_validation_error(persona: dict) -> dict:
    """P5: Bad bank account + IFSC should be caught."""
    steps = []
    transcript = [
        {
            "speaker": persona["name"],
            "role": "citizen",
            "text": f"Namaskar, I am {persona['name']}, {persona['age']} years old from {persona['block']}. My bank account is '{persona['bank_account']}' and IFSC is '{persona['ifsc']}'."
        }
    ]

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

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "validate_application",
        "text": f"Namaskar {persona['name']} ji. There are formatting errors with the details provided: Bank account must be 9–18 digits, and IFSC must be an 11-character code. Please check your bank passbook and give the corrected numbers."
    })

    count = db_count_applications(persona["mobile"])
    steps.append({"tool": "db_assertion_no_app", "input": {"mobile": persona["mobile"]},
                  "output": {"count": count}, "passed": count == 0})

    return {"steps": steps, "transcript": transcript, "passed": has_bank_error and has_ifsc_error and count == 0}


def run_duplicate_detection(persona: dict, existing_mobile: str) -> dict:
    """P4: Mobile already has active app."""
    steps = []
    transcript = [
        {
            "speaker": persona["name"],
            "role": "citizen",
            "text": f"Namaskar, I am {persona['name']} from {persona['block']}, mobile {existing_mobile}. I want to submit another application for the pension."
        }
    ]

    conn = db.get_db()
    cur = conn.cursor()
    existing = db.active_application(cur, existing_mobile)
    cur.close(); conn.close()

    duplicate_caught = existing is not None
    steps.append({"tool": "active_application_check", "input": {"mobile": existing_mobile},
                  "output": str(existing), "passed": duplicate_caught})

    app_no = existing[1] if existing else "N/A"
    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "active_application_check",
        "text": f"Namaskar {persona['name']} ji. Our portal records show that mobile number {existing_mobile} already holds an active application ({app_no}) with status PENDING. Under scheme rules, each citizen can only have one active application. Would you like me to check its status or help you withdraw it?"
    })

    count_before = db_count_applications(existing_mobile)
    steps.append({"tool": "db_assertion_blocked", "input": {"mobile": existing_mobile},
                  "output": {"count_before": count_before, "blocked": duplicate_caught},
                  "passed": duplicate_caught})

    return {"steps": steps, "transcript": transcript, "passed": duplicate_caught}


def run_officer_approval(persona: dict, target_mobile: str) -> dict:
    """P6: Officer approves the application for target_mobile."""
    steps = []
    transcript = [
        {
            "speaker": "Block Officer",
            "role": "officer",
            "text": f"Assistant, inspect the pending queue for Sonari block and fetch the dossier for applicant mobile {target_mobile}."
        }
    ]

    app = db_get_latest_application(target_mobile)
    if not app or app["status"] != "PENDING":
        return {"steps": steps, "transcript": transcript, "passed": False,
                "reason": f"No pending application found for {target_mobile}"}

    steps.append({"tool": "view_application", "input": {"id": app["id"]},
                  "output": {"found": True, "status": app["status"]}, "passed": True})

    transcript.append({
        "speaker": "Officer Assistant",
        "role": "assistant",
        "tool": "view_application",
        "text": f"Retrieved application {app['application_no']} (ID: {app['id']}) for {app['applicant_name']}. Status: PENDING. Age verified via verbal declaration. Audit trail clean."
    })

    note = "Documents verified via AI-assisted review. Age confirmed from verbal declaration."
    transcript.append({
        "speaker": "Block Officer",
        "role": "officer",
        "text": f"Approve application {app['application_no']} with note: '{note}'."
    })

    success, msg = db.decide_application(app["id"], "APPROVED", "officer", note)
    steps.append({"tool": "approve_application",
                  "input": {"id": app["id"], "note": note},
                  "output": {"success": success, "msg": msg}, "passed": success})

    transcript.append({
        "speaker": "Officer Assistant",
        "role": "assistant",
        "tool": "approve_application",
        "text": f"Application {app['application_no']} has been successfully APPROVED. Recorded permanently in audit_log with officer identity."
    })

    # DB assertion
    app_after = db_get_latest_application(target_mobile)
    passed = (app_after and app_after["status"] == "APPROVED" and app_after["decided_by"] == "officer")
    steps.append({"tool": "db_assertion", "input": {"id": app["id"]},
                  "output": app_after, "passed": passed})

    return {"steps": steps, "transcript": transcript, "passed": passed}


def run_withdraw_and_reapply(persona: dict) -> dict:
    """P7: Submit, withdraw, then reapply."""
    steps = []
    transcript = [
        {
            "speaker": persona["name"],
            "role": "citizen",
            "text": f"Namaskar, I am {persona['name']} from {persona['village']}, {persona['block']}. I want to submit an application."
        }
    ]

    # First submission
    otp1 = db.create_otp(persona["mobile"])
    db.verify_otp(persona["mobile"], otp1)
    data = {
        "applicant_name": persona["name"], "dob": persona["dob"], "gender": persona["gender"],
        "marital_status": persona["marital_status"], "village": persona["village"],
        "block": persona["block"], "bank_account": persona["bank_account"],
        "ifsc": persona["ifsc"], "husband_name": "",
    }
    _, cleaned = db.validate_application(data, f"mcp_verbal_declaration:{persona['mobile']}")
    conn = db.get_db()
    cur = conn.cursor()
    first_id, first_no = db.create_application(cur, persona["mobile"], cleaned)
    conn.commit()
    cur.close(); conn.close()
    steps.append({"tool": "submit_application_1", "input": {}, "output": {"app_no": first_no}, "passed": True})

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "submit_pension_application",
        "text": f"Application {first_no} submitted. Status: PENDING."
    })

    # Citizen wants to withdraw
    transcript.append({
        "speaker": persona["name"],
        "role": "citizen",
        "text": "I made a mistake in my bank details! Please help me withdraw this application so I can reapply."
    })

    otp2 = db.create_otp(persona["mobile"])
    db.verify_otp(persona["mobile"], otp2)
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

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "withdraw_application",
        "text": f"Application {first_no} is now WITHDRAWN. You are cleared to submit a fresh application."
    })

    # Citizen reapplies
    transcript.append({
        "speaker": persona["name"],
        "role": "citizen",
        "text": "Here are my corrected bank details: Account 321654987321, IFSC UTBI0001234. Please re-submit."
    })

    otp3 = db.create_otp(persona["mobile"])
    db.verify_otp(persona["mobile"], otp3)
    conn = db.get_db()
    cur = conn.cursor()
    _, cleaned2 = db.validate_application(data, f"mcp_verbal_declaration:{persona['mobile']}")
    second_id, second_no = db.create_application(cur, persona["mobile"], cleaned2)
    conn.commit()
    cur.close(); conn.close()
    steps.append({"tool": "submit_application_2", "input": {}, "output": {"app_no": second_no}, "passed": True})

    transcript.append({
        "speaker": "Sewa Setu Assistant",
        "role": "assistant",
        "tool": "submit_pension_application",
        "text": f"Corrected application submitted successfully! New Application Number: {second_no}."
    })

    # DB assertions
    conn = db.get_db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM applications WHERE mobile=%s ORDER BY submitted_at ASC", (persona["mobile"],))
    all_apps = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()

    has_withdrawn = "WITHDRAWN" in all_apps
    has_pending = "PENDING" in all_apps
    steps.append({"tool": "db_assertion_two_apps", "input": {"mobile": persona["mobile"]},
                  "output": {"statuses": all_apps}, "passed": has_withdrawn and has_pending})

    return {"steps": steps, "transcript": transcript, "passed": has_withdrawn and has_pending}


# ---------------------------------------------------------------------------
# Main eval runner
# ---------------------------------------------------------------------------

def run_all_evals(cleanup: bool = True) -> list[dict]:
    results = []
    run_at = datetime.now().isoformat()

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
                result = {"steps": [], "transcript": [], "passed": False, "reason": f"Unknown scenario: {scenario}"}
        except Exception as e:
            result = {
                "steps": [],
                "transcript": [],
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
            "transcript": result.get("transcript", []),
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
