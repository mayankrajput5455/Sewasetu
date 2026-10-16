# 3-Minute Loom Video Script & Presentation Guide

**Project:** Sewa Setu AI Assistant & MCP Integration  
**Presenter:** Antigravity Engineering  
**Target Duration:** 3 minutes (180 seconds)  
**Video Format:** Face bubble + screen recording (1080p)

---

## Visual Presentation Outline

```
[0:00 - 0:30] Introduction & Core Principle ("Sewa Setu as System of Record")
[0:30 - 1:15] Architecture Walkthrough: Two Isolated MCP Servers + OAuth 2.1
[1:15 - 2:15] Live Demo: /eval-dashboard & Persona Results (8/8 Passed)
[2:15 - 2:45] Design Insights & Failure Modes Caught by Evals
[2:45 - 3:00] Conclusion & Submission Deliverables
```

---

## Detailed Script (Word-for-Word Guide)

### Part 1: Introduction (0:00 – 0:30)
> **[Camera: Face + Portal Home Screen]**  
> *"Hello! Today I'm presenting the Sewa Setu AI Assistant integration for the Department of Social Welfare, Government of Purvanchal.  
> The core directive from the Director was clear: **Sewa Setu must remain the single system of record**.  
> Any AI assistant acting for a citizen or a block officer must enforce the exact same business logic, validation rules, 15-day statutory SLA, and audit trails as the web portal."*

### Part 2: Architecture & Security Boundary (0:30 – 1:15)
> **[Camera: Split Screen — citizen_mcp.py & officer_mcp.py Architecture Diagram]**  
> *"To ensure safety and security, we built **two completely separate Model Context Protocol (MCP) servers** over Streamable HTTP:  
> 1. **Citizen MCP Server on port 8001**: Offers tools for scheme inquiry, pre-eligibility checks, mobile OTP verification, application submission, status checks, and withdrawals.  
> 2. **Officer MCP Server on port 8002**: Offers tools for block queue inspection, viewing full dossiers with audit history, and recording approvals or rejections with mandatory notes.  
> 
> By running these as distinct endpoints, a citizen's assistant literally cannot see or access officer decision tools—preventing prompt-injection privilege escalation at the protocol level. Both servers enforce OAuth 2.1 RS256 token verification against the portal's JWKS endpoint."*

### Part 3: The Evaluation Suite & Dashboard (1:15 – 2:15)
> **[Camera: Live browser open at http://localhost:8000/eval-dashboard]**  
> *"Here is the live **Evaluation Dashboard** at `/eval-dashboard`.  
> Rather than evaluating LLMs on conversational text alone, our eval suite simulates multi-turn conversations across 8 realistic rural personas and verifies **real PostgreSQL database state**.  
> As you can see, we have an **8 out of 8 (100%) pass rate**:  
> - **Phuleswari Baruah (P1)**: An eligible 71-year-old widow from Sonari; successfully verified via OTP and created in `PENDING` state with an audit trail.  
> - **Bhaben Gogoi (P2)**: Age 58; caught at pre-flight check because he is under 60.  
> - **Dipali Das (P3)**: Ineligible block; guided to the 6 notified blocks.  
> - **Hemanta Saikia (P4)**: Blocked from submitting a duplicate active application.  
> - **Tara Begum (P5 / P5B)**: Caught typos in bank account and IFSC format; accepted upon correction.  
> - **Officer Verification (P6)**: Block officer reviews Phuleswari's application and approves it with a note, updating the database status to `APPROVED`.  
> - **Kalidas Bora (P7)**: Successfully tests the withdrawal and re-application workflow."*

### Part 4: Key Insights & Decisions (2:15 – 2:45)
> **[Camera: Scroll to 'Architectural Insights & Design Decisions' on Dashboard]**  
> *"Running these evaluations revealed critical insights:  
> 1. Without strict pre-flight validation, LLMs frequently attempt to submit invalid IFSC codes or incomplete dates.  
> 2. Hard protocol isolation between citizen and officer tools is far more robust than system prompt instructions.  
> 3. Real database assertions are essential to ensure audit logs and portal user credentials are properly initialized."*

### Part 5: Conclusion & Access (2:45 – 3:00)
> **[Camera: Face + Repository]**  
> *"All code, Docker configurations, officer credentials (`admin` / `sewasetu123`), Design Note, and the complete git commit history are packaged in the `sewasetu-assistant` repository. Thank you!"*
