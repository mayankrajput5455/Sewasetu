# -*- coding: utf-8 -*-
"""
assistant.py — Interactive AI Agent Engine for Sewa Setu.
Allows citizens and block officers to converse in natural language.
The agent parses user intent, asks clarifying questions, and autonomously
executes MCP tools against portal_db.
"""

import os
import sys
import re
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_servers"))
import portal_db as db


def handle_citizen_message(message: str, session_state: dict) -> dict:
    """
    Stateful conversational agent for citizens.
    session_state tracks:
      - step: 'initial', 'awaiting_otp', 'collecting_bank', 'submitted', etc.
      - draft: dict of collected applicant details
      - last_otp: generated OTP
    """
    msg = message.strip()
    msg_lower = msg.lower()
    state = session_state.get("step", "initial")
    draft = session_state.get("draft", {})
    tools_called = []
    reply = ""

    # Check for status inquiry
    if any(k in msg_lower for k in ["status", "check my", "track"]):
        mob_match = re.search(r"\b[6-9]\d{9}\b", msg)
        mobile = mob_match.group(0) if mob_match else draft.get("mobile")
        if mobile:
            tools_called.append({"tool": "check_my_status", "input": {"mobile": mobile}})
            app = db.get_application_by_mobile(mobile)
            if app:
                reply = (
                    f"📋 **Application Status for {mobile}:**\n\n"
                    f"- **Application No:** {app['application_no']}\n"
                    f"- **Applicant Name:** {app['applicant_name']}\n"
                    f"- **Block:** {app['block']}\n"
                    f"- **Status:** `{app['status']}`\n"
                    f"- **Submitted At:** {app['submitted_at']}\n"
                )
                if app['status'] == 'PENDING':
                    reply += "\nYour application is being reviewed under the 15-day statutory SLA."
            else:
                reply = f"No active or past pension application found for mobile number **{mobile}**."
            return {"reply": reply, "state": session_state, "tools_called": tools_called}
        else:
            session_state["step"] = "awaiting_status_mobile"
            return {
                "reply": "I'd be glad to check your application status. What is your 10-digit registered mobile number?",
                "state": session_state,
                "tools_called": []
            }

    if state == "awaiting_status_mobile":
        mob_match = re.search(r"\b[6-9]\d{9}\b", msg)
        if mob_match:
            mobile = mob_match.group(0)
            app = db.get_application_by_mobile(mobile)
            session_state["step"] = "initial"
            if app:
                reply = (
                    f"📋 **Application Status for {mobile}:**\n\n"
                    f"- **Application No:** {app['application_no']}\n"
                    f"- **Applicant:** {app['applicant_name']}\n"
                    f"- **Block:** {app['block']}\n"
                    f"- **Status:** `{app['status']}`\n"
                )
            else:
                reply = f"No application found for mobile **{mobile}**."
            return {"reply": reply, "state": session_state, "tools_called": [{"tool": "check_my_status", "input": {"mobile": mobile}}]}
        else:
            return {"reply": "Please provide a valid 10-digit Indian mobile number (e.g., 9876543201).", "state": session_state, "tools_called": []}

    # Check for withdrawal request
    if any(k in msg_lower for k in ["withdraw", "cancel"]):
        mob_match = re.search(r"\b[6-9]\d{9}\b", msg)
        mobile = mob_match.group(0) if mob_match else draft.get("mobile")
        if mobile:
            app = db.get_application_by_mobile(mobile)
            if app and app["status"] == "PENDING":
                code = db.create_otp(mobile)
                session_state["step"] = "awaiting_withdraw_otp"
                session_state["draft"]["mobile"] = mobile
                session_state["draft"]["app_id"] = app["id"]
                session_state["draft"]["app_no"] = app["application_no"]
                tools_called.append({"tool": "request_otp", "input": {"mobile": mobile}, "output": f"OTP sent: {code}"})
                reply = (
                    f"To withdraw application **{app['application_no']}**, I have sent a 6-digit OTP to **{mobile}**.\n\n"
                    f"Please enter the OTP to confirm withdrawal."
                )
                return {"reply": reply, "state": session_state, "tools_called": tools_called}
            else:
                reply = f"No pending application found for **{mobile}** eligible for withdrawal."
                return {"reply": reply, "state": session_state, "tools_called": []}

    if state == "awaiting_withdraw_otp":
        otp_match = re.search(r"\b\d{6}\b", msg)
        if otp_match:
            otp = otp_match.group(0)
            mobile = draft.get("mobile")
            app_id = draft.get("app_id")
            ok, reason = db.verify_otp(mobile, otp)
            tools_called.append({"tool": "verify_otp", "input": {"code": otp}, "output": {"ok": ok}})
            if ok:
                conn = db.get_db()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE applications SET status='WITHDRAWN', decided_at=%s, decided_by='APPLICANT' "
                    "WHERE id=%s AND status='PENDING' RETURNING application_no",
                    (db.now_ist(), app_id)
                )
                row = cur.fetchone()
                if row:
                    db.write_audit(cur, app_id, "WITHDRAW", f"applicant:{mobile}", "Withdrawn via conversational assistant")
                    conn.commit()
                cur.close(); conn.close()
                session_state["step"] = "initial"
                reply = f"✅ Application **{draft.get('app_no')}** has been successfully **WITHDRAWN**. You may now submit a fresh application whenever ready."
            else:
                reply = f"❌ {reason} Please re-enter the 6-digit OTP."
            return {"reply": reply, "state": session_state, "tools_called": tools_called}

    # Intent: Check scheme rules / info
    if any(k in msg_lower for k in ["what is", "rules", "scheme", "benefit", "how much"]):
        tools_called.append({"tool": "get_scheme_info", "input": {}})
        reply = (
            "🏛️ **Sewa Setu Old Age Pension Scheme**\n\n"
            "- **Benefit:** ₹250 per month direct bank transfer.\n"
            "- **Age Requirement:** 60 years or older.\n"
            "- **Eligible Blocks:** Sonari, Rajapara, Dhemaji Pathar, Borgaon, Namti, Khelua.\n"
            "- **Documents Needed:** Any government age-proof ID and bank passbook.\n\n"
            "Would you like to apply now? Tell me your name, age/DOB, and village/block!"
        )
        return {"reply": reply, "state": session_state, "tools_called": tools_called}

    # Step: Awaiting OTP verification for application
    if state == "awaiting_otp":
        otp_match = re.search(r"\b\d{6}\b", msg)
        if otp_match:
            code = otp_match.group(0)
            mobile = draft.get("mobile")
            ok, reason = db.verify_otp(mobile, code)
            tools_called.append({"tool": "verify_otp", "input": {"mobile": mobile, "code": code}, "output": {"ok": ok, "reason": reason}})
            if ok:
                session_state["otp_verified"] = True
                # Check if we already have bank details
                if draft.get("bank_account") and draft.get("ifsc"):
                    # Proceed straight to submission!
                    return _execute_submission(draft, session_state, tools_called)
                else:
                    session_state["step"] = "collecting_bank"
                    reply = (
                        "✅ **Mobile verified successfully!**\n\n"
                        "Now, please provide your **Bank Account Number** and **IFSC code** from your bank passbook (e.g., `Account: 123456789012, IFSC: SBIN0003077`)."
                    )
                    return {"reply": reply, "state": session_state, "tools_called": tools_called}
            else:
                reply = f"❌ {reason} Please check the SMS and re-enter the 6-digit code."
                return {"reply": reply, "state": session_state, "tools_called": tools_called}
        else:
            return {
                "reply": "Please enter the 6-digit verification code sent to your mobile (e.g. `123456`).",
                "state": session_state,
                "tools_called": []
            }

    # Step: Collecting Bank details
    if state == "collecting_bank":
        # Extract IFSC
        ifsc_match = re.search(r"\b[A-Za-z]{4}0[A-Za-z0-9]{6}\b", msg)
        if ifsc_match:
            draft["ifsc"] = ifsc_match.group(0).upper()
        # Extract bank account digits (9 to 18 digits)
        acct_match = re.search(r"\b\d{9,18}\b", msg)
        if acct_match:
            draft["bank_account"] = acct_match.group(0)

        if draft.get("bank_account") and draft.get("ifsc"):
            return _execute_submission(draft, session_state, tools_called)
        else:
            missing = []
            if not draft.get("bank_account"): missing.append("9–18 digit Bank Account Number")
            if not draft.get("ifsc"): missing.append("11-character IFSC code (e.g., SBIN0003077)")
            return {
                "reply": f"I still need your: {', '.join(missing)}. Please check your passbook and provide them.",
                "state": session_state,
                "tools_called": []
            }

    # Step: Initial / Information extraction for application
    # Parse applicant information from free text
    extracted = _extract_applicant_entities(msg)
    draft.update(extracted)
    session_state["draft"] = draft

    # Pre-flight check if we have DOB and block
    if draft.get("dob") and draft.get("block"):
        tools_called.append({"tool": "check_eligibility", "input": {"dob": draft["dob"], "block": draft["block"], "mobile": draft.get("mobile", "")}})
        elig = db.check_eligibility(draft["dob"], draft["block"], draft.get("mobile"))
        if not elig["eligible"]:
            reply = f"⚠️ **Eligibility Notice:**\n" + "\n".join(f"- {issue}" for issue in elig["issues"])
            if elig.get("notes"):
                reply += "\n" + "\n".join(f"- {n}" for n in elig["notes"])
            session_state["step"] = "initial"
            return {"reply": reply, "state": session_state, "tools_called": tools_called}

    # If mobile is present but not verified, request OTP
    if draft.get("mobile") and not session_state.get("otp_verified"):
        # Check duplicate active app
        conn = db.get_db()
        cur = conn.cursor()
        existing = db.active_application(cur, draft["mobile"])
        cur.close(); conn.close()
        if existing:
            tools_called.append({"tool": "check_duplicate", "input": {"mobile": draft["mobile"]}, "output": f"Found existing {existing[1]}"})
            reply = (
                f"⚠️ Mobile number **{draft['mobile']}** already has an active application (**{existing[1]}**) with status **{existing[2]}**.\n\n"
                f"Under scheme rules, each mobile can hold only one active application. You can track it by saying 'status', or withdraw it if you need to submit fresh details."
            )
            session_state["step"] = "initial"
            return {"reply": reply, "state": session_state, "tools_called": tools_called}

        code = db.create_otp(draft["mobile"])
        session_state["step"] = "awaiting_otp"
        tools_called.append({"tool": "request_otp", "input": {"mobile": draft["mobile"]}, "output": f"OTP created: {code}"})
        reply = (
            f"Namaskar {draft.get('name', '')}! I have registered your initial details.\n\n"
            f"📲 I have sent a 6-digit OTP to **{draft['mobile']}**. Please type the OTP to verify your phone."
        )
        return {"reply": reply, "state": session_state, "tools_called": tools_called}

    # Ask for missing required fields
    missing_fields = []
    if not draft.get("name"): missing_fields.append("Full Name")
    if not draft.get("dob"): missing_fields.append("Date of Birth (DD/MM/YYYY) or Age")
    if not draft.get("block"): missing_fields.append("Block (Sonari, Rajapara, Dhemaji Pathar, Borgaon, Namti, Khelua)")
    if not draft.get("mobile"): missing_fields.append("10-digit Mobile Number")

    if missing_fields:
        reply = (
            f"Namaskar! To assist you with the pension application, please provide:\n"
            + "\n".join(f"- **{f}**" for f in missing_fields)
        )
        return {"reply": reply, "state": session_state, "tools_called": tools_called}

    return {"reply": "How can I help you with your pension application today?", "state": session_state, "tools_called": []}


def _execute_submission(draft: dict, session_state: dict, tools_called: list) -> dict:
    data = {
        "applicant_name": draft.get("name", "Applicant"),
        "dob": draft.get("dob", "01/01/1950"),
        "gender": draft.get("gender", "Female"),
        "marital_status": draft.get("marital_status", "Widowed"),
        "husband_name": draft.get("husband_name", ""),
        "village": draft.get("village", draft.get("block", "Sonari")),
        "block": draft.get("block", "Sonari"),
        "bank_account": draft.get("bank_account", ""),
        "ifsc": draft.get("ifsc", ""),
    }
    doc_path = f"mcp_verbal_declaration:{draft.get('mobile')}"
    errors, cleaned = db.validate_application(data, doc_path)

    if errors:
        tools_called.append({"tool": "submit_pension_application", "input": data, "output": {"errors": errors}, "passed": False})
        session_state["step"] = "collecting_bank"
        reply = "⚠️ **Validation Notice:**\n" + "\n".join(f"- {e}" for e in errors) + "\nPlease provide corrected details."
        return {"reply": reply, "state": session_state, "tools_called": tools_called}

    conn = db.get_db()
    cur = conn.cursor()
    new_id, app_no = db.create_application(cur, draft["mobile"], cleaned)
    conn.commit()
    cur.close(); conn.close()

    tools_called.append({"tool": "submit_pension_application", "input": data, "output": {"app_no": app_no, "id": new_id}, "passed": True})
    session_state["step"] = "submitted"
    session_state["submitted_app_no"] = app_no

    reply = (
        f"🎉 **Congratulations {cleaned['applicant_name']}!**\n\n"
        f"Your Old Age Pension application has been **successfully submitted** to the Sewa Setu portal.\n\n"
        f"- **Application Number:** `{app_no}`\n"
        f"- **Status:** `PENDING` (Under statutory 15-day SLA review)\n"
        f"- **Block:** {cleaned['block']}\n"
        f"- **Benefit:** ₹250/month to Account `****{cleaned['bank_account'][-4:]}`\n\n"
        f"You can check your status anytime using your mobile number."
    )
    return {"reply": reply, "state": session_state, "tools_called": tools_called}


def _extract_applicant_entities(text: str) -> dict:
    """Extract name, DOB/age, block, mobile, bank account, IFSC from user speech."""
    out = {}
    # Name patterns
    name_m = re.search(r"(?:name is|i am|myself)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text, re.IGNORECASE)
    if name_m:
        out["name"] = name_m.group(1).title()

    # Mobile pattern
    mob_m = re.search(r"\b[6-9]\d{9}\b", text)
    if mob_m:
        out["mobile"] = mob_m.group(0)

    # Age or DOB
    dob_m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", text)
    if dob_m:
        out["dob"] = dob_m.group(1).replace("-", "/")
    else:
        age_m = re.search(r"\b(?:age|aged|am)\s+(\d{2})\b", text, re.IGNORECASE)
        if age_m:
            age = int(age_m.group(1))
            approx_year = 2026 - age
            out["dob"] = f"01/01/{approx_year}"

    # Block
    for b in db.BLOCKS:
        if b.lower() in text.lower():
            out["block"] = b
            break

    # Village
    vil_m = re.search(r"(?:village|from|at)\s+([A-Za-z]+(?:\s+[A-Za-z]+)*)", text, re.IGNORECASE)
    if vil_m and not out.get("village"):
        candidate = vil_m.group(1).strip()
        if candidate.lower() not in [b.lower() for b in db.BLOCKS]:
            out["village"] = candidate

    # Gender & Marital status
    if "widow" in text.lower():
        out["marital_status"] = "Widowed"
        out["gender"] = "Female"
    elif "married" in text.lower():
        out["marital_status"] = "Married"
    if "female" in text.lower() or "mrs" in text.lower() or "shrimati" in text.lower():
        out["gender"] = "Female"
    elif "male" in text.lower() or "mr" in text.lower() or "shri" in text.lower():
        out["gender"] = "Male"

    # Bank account
    acct_m = re.search(r"\b\d{9,18}\b", text)
    if acct_m and acct_m.group(0) != out.get("mobile"):
        out["bank_account"] = acct_m.group(0)

    # IFSC
    ifsc_m = re.search(r"\b[A-Za-z]{4}0[A-Za-z0-9]{6}\b", text)
    if ifsc_m:
        out["ifsc"] = ifsc_m.group(0).upper()

    return out


def handle_officer_message(message: str, username: str = "officer") -> dict:
    """Agent for block officers inspecting queue and approving applications."""
    msg = message.strip()
    msg_lower = msg.lower()
    tools_called = []

    # Queue query
    if any(k in msg_lower for k in ["queue", "pending", "list", "show"]):
        block_filter = None
        for b in db.BLOCKS:
            if b.lower() in msg_lower:
                block_filter = b
                break
        tools_called.append({"tool": "list_my_queue", "input": {"block": block_filter}})
        apps = db.list_pending_queue(block=block_filter, limit=10)
        if not apps:
            reply = f"No pending applications found in queue for block: **{block_filter or 'All Blocks'}**."
        else:
            lines = [f"📋 **Pending Applications Queue ({len(apps)} shown):**\n"]
            for a in apps:
                flag = "🔴 OVERDUE" if a.get("overdue") else "🟡 PENDING"
                lines.append(f"- **{a['application_no']}** | {a['applicant_name']} | {a['village']}, {a['block']} | Age: {a.get('age', 'N/A')} | {flag}")
            reply = "\n".join(lines)
        return {"reply": reply, "tools_called": tools_called}

    # Approval
    if "approve" in msg_lower:
        app_match = re.search(r"\bSSP\d+\b", msg, re.IGNORECASE)
        app_no = app_match.group(0).upper() if app_match else None
        note = "Verified applicant identity and age proof via officer assistant."
        note_m = re.search(r"note[:\s]+['\"]?([^'\"]+)['\"]?", msg, re.IGNORECASE)
        if note_m:
            note = note_m.group(1).strip()

        if app_no:
            conn = db.get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, status, applicant_name FROM applications WHERE application_no=%s", (app_no,))
            row = cur.fetchone()
            if row:
                app_id, st, name = row
                if st == "PENDING":
                    cur.execute(
                        "UPDATE applications SET status='APPROVED', decided_at=%s, decided_by=%s "
                        "WHERE id=%s",
                        (db.now_ist(), username, app_id)
                    )
                    db.write_audit(cur, app_id, "APPROVED", f"officer:{username}", note)
                    conn.commit()
                    tools_called.append({"tool": "approve_application", "input": {"app_no": app_no, "note": note}, "passed": True})
                    reply = f"✅ Application **{app_no}** for **{name}** has been **APPROVED**.\n\n- **Decided By:** `officer:{username}`\n- **Note:** *{note}*\n- **Audit Log:** Recorded permanently."
                else:
                    reply = f"Application **{app_no}** is already in status `{st}` and cannot be approved."
            else:
                reply = f"Application **{app_no}** not found."
            cur.close(); conn.close()
            return {"reply": reply, "tools_called": tools_called}
        else:
            return {"reply": "Please specify which application number to approve (e.g. `Approve SSP26338161 with note: Verified`).", "tools_called": []}

    # Reject
    if "reject" in msg_lower:
        app_match = re.search(r"\bSSP\d+\b", msg, re.IGNORECASE)
        app_no = app_match.group(0).upper() if app_match else None
        reason = "Rejected upon field verification."
        reason_m = re.search(r"reason[:\s]+['\"]?([^'\"]+)['\"]?", msg, re.IGNORECASE)
        if reason_m:
            reason = reason_m.group(1).strip()

        if app_no:
            conn = db.get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, status, applicant_name FROM applications WHERE application_no=%s", (app_no,))
            row = cur.fetchone()
            if row:
                app_id, st, name = row
                if st == "PENDING":
                    cur.execute(
                        "UPDATE applications SET status='REJECTED', decided_at=%s, decided_by=%s "
                        "WHERE id=%s",
                        (db.now_ist(), username, app_id)
                    )
                    db.write_audit(cur, app_id, "REJECTED", f"officer:{username}", reason)
                    conn.commit()
                    tools_called.append({"tool": "reject_application", "input": {"app_no": app_no, "reason": reason}, "passed": True})
                    reply = f"🚫 Application **{app_no}** for **{name}** has been **REJECTED**.\n\n- **Reason:** *{reason}*\n- **Decided By:** `officer:{username}`"
                else:
                    reply = f"Application **{app_no}** is already in status `{st}`."
            else:
                reply = f"Application **{app_no}** not found."
            cur.close(); conn.close()
            return {"reply": reply, "tools_called": tools_called}

    return {
        "reply": "Block Officer Assistant here. You can ask me to:\n- *'Show pending applications for Sonari'*\n- *'Approve SSP26xxxx with note: Verified age proof'*\n- *'Reject SSP26xxxx with reason: Under age'*",
        "tools_called": []
    }
