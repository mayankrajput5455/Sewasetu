# Sewa Setu AI Assistant — Design Note

**Department of Social Welfare, Government of Purvanchal**  
*Project: AI Assistant Integration via Model Context Protocol (MCP)*

---

## The 3 Design Decisions That Mattered Most

### 1. Sewa Setu Remains the System of Record with Shared Business Logic
In public service delivery, creating a separate "shadow database" or divergent validation rules for AI channels leads to regulatory failure. We designed `portal_db.py` to enforce the exact same business rules and validation logic (`validate_application`, minimum age 60, valid blocks, IFSC verification, 15-day statutory SLA deemed approval, and single active application per mobile) as the core Sewa Setu portal. 
- Applications submitted through the Citizen MCP server immediately appear in the officer queue and status portal.
- All actions are logged into `audit_log` with appropriate actor stamps (`applicant:<mobile>` or `officer:<username>`).
- If business rules change in Sewa Setu, both the web portal and MCP assistants remain synchronized.

### 2. Complete Architectural Separation of Citizen and Officer Endpoints
Rather than a single MCP server with role-based tool filtering, we separated the endpoints into two isolated servers:
- **Citizen MCP Server (`/mcp` on port 8001, scope `citizen`)**: Contains only applicant tools (`get_scheme_info`, `check_eligibility`, `request_otp`, `submit_pension_application`, `get_my_application_status`, `withdraw_my_application`).
- **Officer MCP Server (`/mcp` on port 8002, scope `officer`)**: Contains administrative tools (`list_my_queue`, `view_application`, `approve_application`, `reject_application`, `get_dashboard_summary`).

**Security Impact:** An AI assistant serving a citizen cannot hallucinate, leak, or be prompt-injected into invoking officer approval tools, because the citizen MCP server does not register or know about officer tools. Each server validates OAuth 2.1 RS256 JWT tokens using the Sewa Setu JWKS (`/.well-known/jwks.json`) and verifies required scopes.

### 3. Database-Asserted Persona Evaluation Suite (`evals/` & `/eval-dashboard`)
LLM evaluations that only grade conversational text are insufficient for government service delivery. We designed the eval suite to run complete scenarios across 8 realistic citizen personas and verify **actual PostgreSQL database state**:
- *Phuleswari Baruah (P1)*: Eligible widow; asserts application created with status `PENDING` and audit action `SUBMIT`.
- *Bhaben Gogoi (P2)*: Age 58; asserts eligibility check fails with clear under-age explanation.
- *Dipali Das (P3)*: Ineligible block; asserts block restriction caught.
- *Hemanta Saikia (P4)*: Duplicate application; asserts blocked by existing active application.
- *Tara Begum (P5 / P5B)*: Invalid bank/IFSC format; asserts validation catches error, then succeeds upon correction.
- *Officer Workflow (P6)*: Reviews P1's application; asserts state updated to `APPROVED` with officer audit trail and notes.
- *Kalidas Bora (P7)*: Withdraw and reapply flow; asserts application transitioned to `WITHDRAWN` and re-application created.

All results, transcripts, step logs, and assertions are rendered on an executive dashboard at `/eval-dashboard`.

---

## The 1 Decision We Would Do Differently With More Time

### Multi-Modal Age-Proof Document Verification via MCP
Currently, the citizen assistant submits an age-proof declaration identifier (`mcp_verbal_declaration:<mobile>`) mirroring kiosk submissions. Given more time, we would build a dedicated file-upload MCP tool using MCP multi-modal image content blocks to allow citizens to upload photos of their voter ID, Aadhaar card, or pension passbook directly within the conversational interface. The server would perform client-side and server-side OCR, cross-check the date of birth extracted from the image against the citizen's stated date of birth, and store the document in `/var/sewasetu/uploads` for the officer's visual review.
