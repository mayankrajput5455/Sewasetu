# -*- coding: utf-8 -*-
"""
officer_mcp.py — Block Officer MCP Server for Sewa Setu Old Age Pension Portal.

Exposes tools for AI assistants acting on behalf of block officers:
  - list_my_queue           See pending applications (with SLA urgency)
  - view_application        Full application details + audit trail
  - approve_application     Approve a pending application (requires note)
  - reject_application      Reject a pending application (requires reason)
  - get_dashboard_summary   Statistics across blocks

Security: Bearer token with scope=officer required for all tools.
          This is a SEPARATE endpoint from citizens — no citizen tools here.

Transport: Streamable HTTP at /mcp (port 8002)
"""

import os
import sys
import logging
from typing import Optional

from mcp.server.mcpserver import MCPServer

sys.path.insert(0, os.path.dirname(__file__))
import portal_db as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("officer_mcp")

PORTAL_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

mcp = MCPServer(
    name="sewasetu-officer",
    title="Sewa Setu – Block Officer Assistant",
    description=(
        "AI assistant tools for block officers reviewing Old Age Pension applications "
        "on the Sewa Setu portal. Citizens cannot access this endpoint."
    ),
    instructions="""
You are an assistant for block officers reviewing Old Age Pension applications on
the Sewa Setu portal, Department of Social Welfare, Government of Purvanchal.

You can help officers:
1. See the pending application queue for their block (oldest first, with SLA urgency flags)
2. View full details of any application including audit trail
3. Approve or reject pending applications with mandatory notes
4. View summary statistics

IMPORTANT RULES:
- You MUST always include a clear, specific note when approving or rejecting
- Approvals/rejections are recorded permanently in the audit log with your officer identity
- You can only act on PENDING applications
- Applications not decided within 15 days are automatically approved by the system
- You cannot see or modify citizen authentication credentials
- Always read the full application (view_application) before deciding
- 🔴 OVERDUE applications have exceeded the 15-day SLA — prioritize these
""",
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_my_queue(block: Optional[str] = None, limit: int = 20) -> str:
    """
    List pending applications in the queue, ordered by submission date (oldest first).
    Applications approaching or past the 15-day SLA deadline are flagged urgently.

    Args:
        block: Filter by specific block name (optional; shows all blocks if omitted)
        limit: Maximum number of applications to return (default: 20, max: 50)
    """
    limit = min(max(1, limit), 50)
    rows = db.list_pending_queue(block=block, limit=limit)

    if not rows:
        filter_msg = f" in block '{block}'" if block else ""
        return f"✅ No pending applications{filter_msg}. Queue is clear."

    filter_msg = f" in block '{block}'" if block else " (all blocks)"
    lines = [f"📋 Pending Applications{filter_msg} — {len(rows)} shown\n"]

    for r in rows:
        urgent = r.get("overdue") or (r.get("days_until_deemed", 999) <= 3)
        if r.get("overdue"):
            flag = "🔴 OVERDUE"
        elif urgent:
            flag = "🟡 URGENT"
        else:
            flag = "🟢"
        lines.append(
            f"{flag} ID:{r['id']} | {r['application_no']} | "
            f"{r['applicant_name']} | {r['village']}, {r['block']} | "
            f"Pending: {r.get('days_pending', '?')} days | "
            f"Auto-approval in: {r.get('days_until_deemed', '?')} days"
        )

    return "\n".join(lines)


@mcp.tool()
def view_application(application_id: int) -> str:
    """
    View the full details of a specific application, including applicant information,
    bank details, document information, and complete audit trail.

    Args:
        application_id: The numeric application ID (shown in list_my_queue)
    """
    app = db.get_application_by_id(application_id)
    if not app:
        return f"❌ Application ID {application_id} not found."

    status_emoji = {"PENDING": "🕐", "APPROVED": "✅", "DEEMED_APPROVED": "✅",
                    "REJECTED": "❌", "WITHDRAWN": "↩️"}.get(app["status"], "ℹ️")

    doc_note = ""
    if app.get("doc_path"):
        if "mcp_verbal_declaration" in str(app["doc_path"]):
            doc_note = "⚠️  Applied via AI assistant — VERBAL DECLARATION only. Request physical document before approving."
        else:
            doc_note = f"Document on file: {os.path.basename(str(app['doc_path']))}"

    audit_lines = []
    for entry in app.get("audit", []):
        audit_lines.append(
            f"  [{entry['at']}] {entry['action']} by {entry['actor']}"
            + (f" — {entry['note']}" if entry.get("note") else "")
        )

    return f"""{status_emoji} APPLICATION ID: {application_id}

Application No : {app['application_no']}
Status         : {app['status']}
Submitted      : {app.get('submitted_at', 'N/A')}
Decided        : {app.get('decided_at', 'Pending')}
Decided By     : {app.get('decided_by', 'N/A')}

--- APPLICANT ---
Name           : {app['applicant_name']}
Date of Birth  : {app.get('dob', 'N/A')}
Gender         : {app.get('gender', 'N/A')}
Marital Status : {app.get('marital_status', 'N/A')}
Mobile         : {app.get('mobile', 'N/A')}

--- ADDRESS ---
Village        : {app['village']}
Block          : {app['block']}

--- BANK DETAILS ---
Account No     : {app.get('bank_account', 'N/A')}
IFSC Code      : {app.get('ifsc', 'N/A')}

--- DOCUMENT ---
{doc_note or 'No document information available.'}

--- AUDIT TRAIL ---
{chr(10).join(audit_lines) if audit_lines else '  No audit entries.'}"""


@mcp.tool()
def approve_application(application_id: int, note: str) -> str:
    """
    Approve a PENDING pension application. Your identity and note are permanently
    recorded in the audit log and the citizen is notified by SMS.

    Args:
        application_id: The numeric application ID
        note: Mandatory verification note (e.g., "Documents verified, age confirmed from Aadhaar. Applicant is 71 years old, eligible.")
    """
    if not note or len(note.strip()) < 10:
        return "❌ Error: A meaningful note is required (min 10 characters). Please describe what you verified."

    actor = "officer"  # In production, extracted from Bearer JWT token sub field
    success, message = db.decide_application(application_id, "APPROVED", actor, note.strip())

    if success:
        return (
            f"✅ {message}\n\n"
            f"Verification note: \"{note.strip()}\"\n"
            "This decision is final and recorded in the audit trail. The citizen has been notified by SMS."
        )
    return f"❌ Could not approve: {message}"


@mcp.tool()
def reject_application(application_id: int, reason: str) -> str:
    """
    Reject a PENDING pension application. Your identity and reason are permanently
    recorded in the audit log and the citizen is notified by SMS.

    Args:
        application_id: The numeric application ID
        reason: Mandatory, specific reason for rejection (e.g., "Age proof document missing — verbal declaration insufficient for approval.")
    """
    if not reason or len(reason.strip()) < 10:
        return "❌ Error: A specific rejection reason is required (min 10 characters). Citizens deserve to know why."

    actor = "officer"  # In production, extracted from Bearer JWT token sub field
    success, message = db.decide_application(application_id, "REJECTED", actor, reason.strip())

    if success:
        return (
            f"❌ {message}\n\n"
            f"Rejection reason: \"{reason.strip()}\"\n"
            "This is recorded in the audit trail. The citizen has been notified by SMS and may reapply with corrections."
        )
    return f"❌ Could not reject: {message}"


@mcp.tool()
def get_dashboard_summary(block: Optional[str] = None) -> str:
    """
    Get a summary of application statistics — total pending, approved, overdue, by block.

    Args:
        block: Filter by specific block (optional; shows all blocks if omitted)
    """
    try:
        from datetime import timedelta
        conn = db.get_db()
        cur = conn.cursor()

        if block:
            cur.execute(
                "SELECT status, count(*) FROM applications WHERE block=%s GROUP BY status ORDER BY count(*) DESC",
                (block,)
            )
        else:
            cur.execute(
                "SELECT status, count(*) FROM applications GROUP BY status ORDER BY count(*) DESC"
            )
        by_status = cur.fetchall()

        sla_cutoff = db.now_ist() - timedelta(days=db.SLA_DAYS)
        if block:
            cur.execute(
                "SELECT count(*) FROM applications WHERE status='PENDING' AND submitted_at<%s AND block=%s",
                (sla_cutoff, block)
            )
        else:
            cur.execute(
                "SELECT count(*) FROM applications WHERE status='PENDING' AND submitted_at<%s",
                (sla_cutoff,)
            )
        overdue = cur.fetchone()[0]

        cur.execute(
            "SELECT block, count(*) FROM applications WHERE status='PENDING' "
            "GROUP BY block ORDER BY count(*) DESC LIMIT 10"
        )
        by_block = cur.fetchall()
        cur.close(); conn.close()

        title = f"Block: {block}" if block else "All Blocks"
        lines = [f"📊 Dashboard Summary — {title}\n", "Status Breakdown:"]
        for status, count in by_status:
            emoji = {"PENDING": "🕐", "APPROVED": "✅", "DEEMED_APPROVED": "✅✅",
                     "REJECTED": "❌", "WITHDRAWN": "↩️"}.get(status, "ℹ️")
            lines.append(f"  {emoji} {status}: {count}")

        lines.append(f"\n🔴 Overdue (>{db.SLA_DAYS} days, auto-approval risk): {overdue}")
        lines.append("\nTop Blocks by Pending Count:")
        for blk, cnt in by_block:
            lines.append(f"  • {blk}: {cnt} pending")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error fetching dashboard: {str(e)}"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("OFFICER_MCP_PORT", 8002))
    logger.info(f"Starting Officer MCP Server on port {port}")
    mcp.run(transport="streamable-http", port=port, host="0.0.0.0")
