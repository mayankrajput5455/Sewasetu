# -*- coding: utf-8 -*-
"""
eval_personas.py — Citizen personas and evaluation scenarios for the Sewa Setu eval suite.

Each persona has:
  - name, age, village, block, backstory, mobile, how they talk
  - intent (what they want to achieve)
  - expected_db_state (what must be true in the database after the conversation)
  - scenario_type (happy path, eligibility failure, duplicate, validation error, officer flow)
"""

PERSONAS = [
    {
        "id": "P1",
        "name": "Phuleswari Baruah",
        "age": 71,
        "dob": "12/05/1955",
        "mobile": "9876543201",
        "gender": "Female",
        "marital_status": "Widowed",
        "husband_name": "Hemanta Baruah",
        "village": "Sonari Gaon",
        "block": "Sonari",
        "bank_account": "123456789012",
        "ifsc": "SBIN0003077",
        "scenario_type": "happy_path",
        "backstory": (
            "Phuleswari is a 71-year-old widow from Sonari village. Her grandson helped her "
            "come to the CSC kiosk. She speaks broken Assamese-Hindi mix, is patient, gives "
            "all details slowly, and has a photo ID for age proof. This is a straightforward "
            "first-time application."
        ),
        "communication_style": (
            "Speaks slowly and hesitates. Says 'haan ji' a lot. Needs the assistant to guide "
            "her step by step. Does not know what IFSC means and will need it explained."
        ),
        "intent": "Apply for the pension for the first time",
        "expected_db_state": {
            "status": "PENDING",
            "applicant_name": "Phuleswari Baruah",
            "block": "Sonari",
            "mobile": "9876543201",
        },
        "expected_outcome": "Application created with PENDING status",
        "eval_checks": [
            "application exists in DB with mobile=9876543201",
            "status is PENDING",
            "block is Sonari",
            "applicant_name contains Baruah",
            "audit_log has SUBMIT action",
        ]
    },
    {
        "id": "P2",
        "name": "Bhaben Gogoi",
        "age": 58,
        "dob": "03/11/1967",
        "mobile": "9876543202",
        "gender": "Male",
        "marital_status": "Married",
        "husband_name": "",
        "village": "Namti Bazar",
        "block": "Namti",
        "bank_account": "987654321098",
        "ifsc": "HDFC0001234",
        "scenario_type": "eligibility_failure_age",
        "backstory": (
            "Bhaben is 58, not yet 60. His daughter thinks he might be eligible. He is "
            "assertive and wants to apply right now even after being told he's ineligible."
        ),
        "communication_style": (
            "Insistent, a bit frustrated. Says 'but I heard everyone gets it'. "
            "Needs a clear, polite explanation why he cannot apply yet."
        ),
        "intent": "Apply for pension despite being underage",
        "expected_db_state": None,  # No application should be created
        "expected_outcome": "Application rejected at eligibility check — age too low",
        "eval_checks": [
            "no application in DB with mobile=9876543202",
            "check_eligibility returns eligible=false",
            "reason mentions age requirement",
        ]
    },
    {
        "id": "P3",
        "name": "Dipali Das",
        "age": 65,
        "dob": "22/08/1961",
        "mobile": "9876543203",
        "gender": "Female",
        "marital_status": "Widowed",
        "husband_name": "Ranjit Das",
        "village": "Guwahati Nagar",
        "block": "Guwahati",  # NOT an eligible block
        "bank_account": "456789012345",
        "ifsc": "PUNB0123456",
        "scenario_type": "eligibility_failure_block",
        "backstory": (
            "Dipali lives in Guwahati which is not covered by the scheme. Her nephew called "
            "an AI assistant to help her. She doesn't understand why her block isn't covered."
        ),
        "communication_style": (
            "Confused and sad. Asks 'why not me?' several times. "
            "Needs the assistant to clearly list the eligible blocks and suggest she verify "
            "her actual block of residence."
        ),
        "intent": "Apply for pension from ineligible block",
        "expected_db_state": None,  # No application should be created
        "expected_outcome": "Eligibility check fails — block not covered",
        "eval_checks": [
            "no application in DB with mobile=9876543203",
            "check_eligibility returns eligible=false",
            "reason mentions block eligibility",
            "response lists eligible blocks",
        ]
    },
    {
        "id": "P4",
        "name": "Hemanta Saikia",
        "age": 68,
        "dob": "15/03/1958",
        "mobile": "9876543201",  # Same mobile as P1 — duplicate scenario
        "gender": "Male",
        "marital_status": "Married",
        "husband_name": "",
        "village": "Namti Char",
        "block": "Namti",
        "bank_account": "111222333444",
        "ifsc": "SBIN0001234",
        "scenario_type": "duplicate_application",
        "backstory": (
            "Hemanta's grandson tries to apply for him, but the mobile number 9876543201 "
            "already has an active application (Phuleswari's from P1). This tests the "
            "duplicate detection logic."
        ),
        "communication_style": "Confident, grandson is tech-savvy and keeps insisting.",
        "intent": "Apply for pension, but mobile number already has active application",
        "expected_db_state": None,  # No NEW application should be created
        "expected_outcome": "Blocked — existing active application on this mobile",
        "eval_checks": [
            "only one application exists for mobile=9876543201",
            "second submission attempt is blocked",
            "response mentions existing application number",
        ]
    },
    {
        "id": "P5",
        "name": "Tara Begum",
        "age": 63,
        "dob": "07/09/1963",
        "mobile": "9876543204",
        "gender": "Female",
        "marital_status": "Widowed",
        "husband_name": "Abdul Karim",
        "village": "Borgaon Char",
        "block": "Borgaon",
        "bank_account": "not-a-number",  # Invalid bank account
        "ifsc": "INVALID",  # Invalid IFSC
        "scenario_type": "validation_error",
        "backstory": (
            "Tara typed her bank account and IFSC incorrectly. She has the passbook but "
            "her grandson misread it. The assistant should catch both errors and prompt "
            "correction before allowing submission."
        ),
        "communication_style": "Cooperative, reads out passbook details when asked.",
        "intent": "Apply for pension with initially incorrect bank details",
        "expected_db_state": None,  # Should fail validation first
        "expected_outcome": "Validation fails — bank account and IFSC format errors caught",
        "eval_checks": [
            "initial submission attempt returns validation errors",
            "errors mention bank account format",
            "errors mention IFSC format",
            "no application created with invalid data",
        ]
    },
    {
        "id": "P5B",
        "name": "Tara Begum (corrected)",
        "age": 63,
        "dob": "07/09/1963",
        "mobile": "9876543204",
        "gender": "Female",
        "marital_status": "Widowed",
        "husband_name": "Abdul Karim",
        "village": "Borgaon Char",
        "block": "Borgaon",
        "bank_account": "543210987654",  # Corrected bank account
        "ifsc": "SBIN0005678",  # Corrected IFSC
        "scenario_type": "validation_error_then_success",
        "backstory": "After correcting bank details, Tara's application should succeed.",
        "communication_style": "Cooperative, reads out passbook details when asked.",
        "intent": "Apply for pension after correcting bank details",
        "expected_db_state": {
            "status": "PENDING",
            "applicant_name": "Tara Begum",
            "block": "Borgaon",
            "mobile": "9876543204",
        },
        "expected_outcome": "Application created after validation error correction",
        "eval_checks": [
            "application exists in DB with mobile=9876543204",
            "status is PENDING",
            "block is Borgaon",
        ]
    },
    {
        "id": "P6",
        "name": "Officer Verification — Phuleswari",
        "role": "officer",
        "target_mobile": "9876543201",
        "scenario_type": "officer_approval",
        "backstory": (
            "Block officer Priya Sharma logs into the officer assistant and reviews "
            "Phuleswari Baruah's application, then approves it with a verification note."
        ),
        "communication_style": "Professional, efficient. Checks details, then decides.",
        "intent": "Officer reviews and approves Phuleswari's pending application",
        "expected_db_state": {
            "status": "APPROVED",
            "decided_by": "officer",
            "applicant_name": "Phuleswari Baruah",
        },
        "expected_outcome": "Application approved by officer; audit trail updated",
        "eval_checks": [
            "application for mobile=9876543201 has status=APPROVED",
            "audit_log has APPROVED action",
            "decided_by contains 'officer'",
        ]
    },
    {
        "id": "P7",
        "name": "Kalidas Bora",
        "age": 72,
        "dob": "01/01/1954",
        "mobile": "9876543205",
        "gender": "Male",
        "marital_status": "Married",
        "husband_name": "",
        "village": "Rajapara Bazar",
        "block": "Rajapara",
        "bank_account": "321654987321",
        "ifsc": "UTBI0001234",
        "scenario_type": "withdraw_and_reapply",
        "backstory": (
            "Kalidas first applied and then realized his bank details are wrong. "
            "He wants to withdraw and reapply with the correct account."
        ),
        "communication_style": "Methodical, follows instructions carefully.",
        "intent": "Withdraw pending application and reapply",
        "expected_db_state": {
            "status": "PENDING",
            "applicant_name": "Kalidas Bora",
            "block": "Rajapara",
        },
        "expected_outcome": "First application withdrawn, second application created",
        "eval_checks": [
            "withdrawn application exists with status=WITHDRAWN for mobile=9876543205",
            "new application exists with status=PENDING for mobile=9876543205",
            "audit_log has WITHDRAW then SUBMIT actions",
        ]
    },
]
