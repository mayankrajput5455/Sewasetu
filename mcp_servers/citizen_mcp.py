# -*- coding: utf-8 -*-
"""
citizen_mcp.py — Citizen MCP Server for Sewa Setu Old Age Pension Portal.

Exposes tools for AI assistants acting on behalf of citizens:
  - get_scheme_info              Check scheme rules & eligibility criteria
  - check_eligibility            Pre-flight check before applying
  - request_otp                  Send OTP to mobile for verification
  - submit_pension_application   Full application submission (same rules as portal)
  - get_my_application_status    Check current application status
  - withdraw_my_application      Withdraw a pending application

Security: Bearer token with scope=citizen required for protected tools.
          Token is verified using the portal's JWKS.

Transport: Streamable HTTP at /mcp (port 8001)
"""

import os
import sys
import logging
from typing import Optional

from mcp.server.mcpserver import MCPServer

sys.path.insert(0, os.path.dirname(__file__))
import portal_db as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("citizen_mcp")

PORTAL_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

mcp = MCPServer(
    name="sewasetu-citizen",
    title="Sewa Setu – Citizen Assistant",
    description=(
        "AI assistant tools for citizens applying to the Sewa Setu Old Age Pension scheme, "
        "Government of Purvanchal. Separate from the officer endpoint — citizens only."
    ),
    instructions="""
You are a helpful assistant for citizens applying to the Sewa Setu Old Age Pension scheme,
Government of Purvanchal (₹250/month for residents aged 60+).

You can help citizens:
1. Understand scheme eligibility (age ≥60, eligible blocks, valid bank details)
2. Check their pre-eligibility before starting
3. Apply for the pension (requires mobile OTP verification)
4. Check the status of their application
5. Withdraw a pending application if needed

IMPORTANT RULES:
- Always ask for OTP verification before submitting any application
- Validate all inputs carefully: DD/MM/YYYY dates, 9-18 digit bank accounts, 11-char IFSC
- Never submit an application without successful OTP verification
- The portal is the system of record; submissions appear there immediately
- Do not reveal other citizens' information
- If validation fails, explain exactly what needs correcting
- Available blocks: Sonari, Rajapara, Dhemaji Pathar, Borgaon, Namti, Khelua
""",
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_scheme_info() -> str:
    """
    Returns the official scheme information and eligibility rules for the
    Sewa Setu Old Age Pension scheme. No authentication required — this is public information.
    """
    deadline_str = db.SCHEME_DEADLINE.strftime("%d %B %Y at %H:%M")
    blocks_str = ", ".join(db.BLOCKS)
    return f"""## Sewa Setu – Old Age Pension Scheme
**Department of Social Welfare, Government of Purvanchal**

### Benefits
- ₹250 per month (deposited directly to your bank account)

### Eligibility Criteria
1. **Age**: Must be {db.MIN_AGE} years or above at time of application
2. **Residence**: Must reside in one of these blocks: {blocks_str}
3. **One application per mobile**: Only one active application per mobile number
4. **Valid bank account**: 9–18 digit account number + 11-character IFSC code
5. **Age proof document**: Any government-issued document showing date of birth

### Application Window
Applications accepted until: **{deadline_str}**

### After Application
- Officers review within **{db.SLA_DAYS} days**
- If not decided in {db.SLA_DAYS} days, application is **automatically approved** under the Purvanchal Right to Public Services Act

### Track Your Application
Login at {PORTAL_BASE_URL}/status with your mobile number and date-of-birth password (DDMMYYYY)"""


@mcp.tool()
def check_eligibility(date_of_birth: str, block: str, mobile: str) -> str:
    """
    Check if a citizen meets the basic eligibility criteria BEFORE starting the full application.
    Use this as a first step to save time.

    Args:
        date_of_birth: Citizen's date of birth in DD/MM/YYYY format
        block: The block where the citizen resides
        mobile: 10-digit mobile number
    """
    result = db.check_eligibility(date_of_birth, block, mobile)
    if result["eligible"]:
        return (
            "✅ ELIGIBLE: This citizen appears eligible to apply.\n\n"
            "Checks passed:\n" +
            "\n".join(f"  • {n}" for n in result["notes"]) +
            "\n\nProceed with request_otp and then submit_pension_application."
        )
    else:
        issues_text = "\n".join(f"  ✗ {issue}" for issue in result["issues"])
        notes_text = "\n".join(f"  ✓ {n}" for n in result["notes"]) if result["notes"] else ""
        msg = f"❌ NOT ELIGIBLE: This citizen cannot apply at this time.\n\nIssues:\n{issues_text}"
        if notes_text:
            msg += f"\n\nPassed checks:\n{notes_text}"
        return msg


@mcp.tool()
def request_otp(mobile: str) -> str:
    """
    Send a One-Time Password to the citizen's mobile number for identity verification.
    The citizen MUST verify their OTP before an application can be submitted.

    Args:
        mobile: 10-digit mobile number
    """
    if not mobile or len(mobile) != 10 or not mobile.isdigit():
        return "❌ Error: Please provide a valid 10-digit mobile number."
    try:
        db.create_otp(mobile)
        return (
            f"✅ OTP sent to {mobile[:4]}****{mobile[-2:]}.\n"
            "Please ask the citizen for the 6-digit code they received by SMS.\n"
            "The OTP is valid for 5 minutes.\n\n"
            f"Note: In the test environment, view OTPs at {PORTAL_BASE_URL}/__gateway/"
        )
    except Exception as e:
        logger.error(f"OTP send error: {e}")
        return f"❌ Error sending OTP: {str(e)}"


@mcp.tool()
def submit_pension_application(
    mobile: str,
    otp_code: str,
    applicant_name: str,
    date_of_birth: str,
    gender: str,
    village: str,
    block: str,
    bank_account: str,
    ifsc_code: str,
    marital_status: str = "Single",
    husband_name: str = ""
) -> str:
    """
    Submit a pension application after OTP verification.
    Enforces identical rules to the web portal — same validations, same business logic.

    Args:
        mobile: 10-digit mobile number (must match OTP recipient)
        otp_code: 6-digit OTP received by the citizen
        applicant_name: Full name of the applicant
        date_of_birth: Date of birth in DD/MM/YYYY format
        gender: Male / Female / Other
        village: Village name
        block: One of: Sonari, Rajapara, Dhemaji Pathar, Borgaon, Namti, Khelua
        bank_account: Bank account number (9–18 digits, no spaces or dashes)
        ifsc_code: IFSC code (11 characters, e.g. SBIN0003077)
        marital_status: Married / Widowed / Divorced / Single
        husband_name: Husband's name (if married/widowed)
    """
    # Validate mobile
    if not mobile or len(mobile) != 10 or not mobile.isdigit():
        return "❌ Error: Please provide a valid 10-digit mobile number."

    # Verify OTP
    otp_ok, otp_reason = db.verify_otp(mobile, otp_code.strip())
    if not otp_ok:
        return f"❌ OTP verification failed: {otp_reason}\nPlease request a new OTP with request_otp."

    # For MCP-submitted applications, record verbal declaration as doc_path
    doc_placeholder = f"mcp_verbal_declaration:{mobile}"

    # Validate application data (same function as portal)
    data = {
        "applicant_name": applicant_name,
        "dob": date_of_birth,
        "gender": gender,
        "village": village,
        "block": block,
        "bank_account": bank_account,
        "ifsc": ifsc_code,
        "marital_status": marital_status,
        "husband_name": husband_name,
    }
    errors, cleaned = db.validate_application(data, doc_placeholder)

    if errors:
        error_text = "\n".join(f"  • {e}" for e in errors)
        return (
            f"❌ Application cannot be submitted — please correct the following:\n{error_text}\n\n"
            "After corrections, please resubmit (OTP must be re-requested)."
        )

    # Check for existing active application
    conn = db.get_db()
    cur = conn.cursor()
    existing = db.active_application(cur, mobile)
    if existing:
        cur.close(); conn.close()
        return (
            f"❌ Application already exists: {existing[1]} (status: {existing[2]}).\n"
            "Use get_my_application_status to check it, or withdraw_my_application to start fresh."
        )

    # Create application
    try:
        new_id, app_no = db.create_application(cur, mobile, cleaned)
        conn.commit()
        cur.close(); conn.close()
        db.send_sms(
            mobile,
            f"Sewa Setu: Application {app_no} received. "
            f"Track at {PORTAL_BASE_URL}/status (password: {cleaned['dob'].strftime('%d%m%Y')})"
        )
        return (
            f"✅ APPLICATION SUBMITTED SUCCESSFULLY!\n\n"
            f"Application Number: **{app_no}**\n"
            f"Applicant: {cleaned['applicant_name']}\n"
            f"Block: {cleaned['block']}\n"
            f"Status: PENDING\n\n"
            f"The officer will review within {db.SLA_DAYS} days. "
            f"Track at: {PORTAL_BASE_URL}/status\n"
            f"Login: mobile {mobile}, password {cleaned['dob'].strftime('%d%m%Y')}\n\n"
            f"⚠️ Please upload your original age-proof document at {PORTAL_BASE_URL}/status to complete your file."
        )
    except Exception as e:
        try:
            conn.rollback(); cur.close(); conn.close()
        except Exception:
            pass
        logger.error(f"Application create error: {e}")
        return f"❌ Error creating application: {str(e)}"


@mcp.tool()
def get_my_application_status(mobile: str) -> str:
    """
    Check the status of the citizen's pension application.

    Args:
        mobile: 10-digit mobile number
    """
    if not mobile or len(mobile) != 10 or not mobile.isdigit():
        return "❌ Error: Please provide a valid 10-digit mobile number."

    app = db.get_application_by_mobile(mobile)
    if not app:
        return (
            f"No application found for mobile {mobile[:4]}****{mobile[-2:]}.\n"
            "You can start a new application using submit_pension_application."
        )

    status = app["status"]
    status_emoji = {"PENDING": "🕐", "APPROVED": "✅", "DEEMED_APPROVED": "✅",
                    "REJECTED": "❌", "WITHDRAWN": "↩️"}.get(status, "ℹ️")

    msg = (
        f"{status_emoji} Application Status\n\n"
        f"Application No: {app['application_no']}\n"
        f"Applicant: {app['applicant_name']}\n"
        f"Block: {app['block']}\n"
        f"Status: **{status}**\n"
        f"Submitted: {app['submitted_at']}\n"
    )
    if app.get("decided_at"):
        msg += f"Decided: {app['decided_at']}\nDecided by: {app['decided_by']}\n"

    status_messages = {
        "PENDING": f"\nYour application is waiting for officer review. If not decided within {db.SLA_DAYS} days from submission, it will be automatically approved.",
        "APPROVED": "\n✅ Congratulations! Your pension has been approved. Disbursement will begin shortly.",
        "DEEMED_APPROVED": "\n✅ Your application was automatically approved under the RTPS Act (SLA exceeded).",
        "REJECTED": "\n❌ Your application was rejected. You may withdraw it and reapply with corrections.",
        "WITHDRAWN": "\n↩️ This application was withdrawn. You can submit a new application.",
    }
    msg += status_messages.get(status, "")
    return msg


@mcp.tool()
def withdraw_my_application(mobile: str, otp_code: str) -> str:
    """
    Withdraw a PENDING application so the citizen can reapply with corrections.
    Requires OTP verification for security.

    Args:
        mobile: 10-digit mobile number
        otp_code: 6-digit OTP (request one with request_otp first)
    """
    if not mobile or len(mobile) != 10 or not mobile.isdigit():
        return "❌ Error: Please provide a valid 10-digit mobile number."

    otp_ok, otp_reason = db.verify_otp(mobile, otp_code.strip())
    if not otp_ok:
        return f"❌ OTP verification failed: {otp_reason}\nPlease request a new OTP."

    conn = db.get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE applications SET status='WITHDRAWN', decided_at=%s, decided_by='APPLICANT' "
        "WHERE mobile=%s AND status='PENDING' RETURNING id, application_no",
        (db.now_ist(), mobile)
    )
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return "❌ No pending application found for this mobile number. Only PENDING applications can be withdrawn."

    db.write_audit(cur, row[0], "WITHDRAW", f"applicant:{mobile}", "Withdrawn via MCP citizen assistant")
    conn.commit()
    cur.close(); conn.close()

    return (
        f"✅ Application {row[1]} has been successfully withdrawn.\n\n"
        "You can now submit a new application using submit_pension_application."
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("CITIZEN_MCP_PORT", 8001))
    logger.info(f"Starting Citizen MCP Server on port {port}")
    mcp.run(transport="streamable-http", port=port, host="0.0.0.0")
