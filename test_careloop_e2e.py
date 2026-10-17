"""
CareLoop AI End-to-End Test Suite
Tests all agent handoffs, Supabase/SQLite data layer, approval workflow, and simulation scenarios.
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import asyncio
from fastapi.testclient import TestClient
import main
from main import app
import database

def run_tests():
    print("=" * 60)
    print("STARTING CARELOOP AI INTEGRATION & RECOVERY ENGINE TESTS")
    print("=" * 60)

    client = TestClient(app)
    # Test 1: Health & Database Mode Check
    print("\n[Test 1] Testing /health and Database status...")
    res = client.get("/health")
    assert res.status_code == 200, f"Health check failed: {res.text}"
    health = res.json()
    print(f"-> Health: {health['status']}, Database Mode: {health['database']['mode']}")
    assert "mode" in health["database"]

    # Test 2: System Stats
    print("\n[Test 2] Testing /api/stats...")
    res = client.get("/api/stats")
    assert res.status_code == 200
    stats = res.json()
    print(f"-> Total appointments: {stats['total_appointments']}, Registered: {stats['registered_patients']}, Recovery rate: {stats['recovery_rate_pct']}%")
    assert stats["total_appointments"] >= 30, "Expected at least 30 pilot appointments"

    # Test 3: List Appointments
    print("\n[Test 3] Testing /api/appointments...")
    res = client.get("/api/appointments")
    assert res.status_code == 200
    apps = res.json()
    print(f"-> Retrieved {len(apps)} appointments")
    assert len(apps) >= 30

    # Test 4: Book New Appointment from Patient Portal
    print("\n[Test 4] Testing Patient Booking POST /api/appointments...")
    new_patient = {
        "patient_name": "Test Zoya Farooq",
        "phone": "0300 9988771",
        "age": 28,
        "gender": "Female",
        "reason": "Routine Antenatal Checkup",
        "date": "2026-10-25",
        "time": "11:30"
    }
    res = client.post("/api/appointments", json=new_patient)
    assert res.status_code == 200, f"Booking failed: {res.text}"
    booked = res.json()["appointment"]
    print(f"-> Successfully booked patient ID: {booked['id']} ({booked['patient_name']})")
    assert booked["status"] == "Scheduled"

    # Test 5: Receptionist Updates Status to Missed
    print("\n[Test 5] Testing Reception Status Update PATCH /api/appointments/{id}/status...")
    res = client.patch(f"/api/appointments/{booked['id']}/status", json={"status": "Missed"})
    assert res.status_code == 200
    print(f"-> Updated {booked['id']} status to 'Missed'")

    # Test 6: Recovery Monitor Scan
    print("\n[Test 6] Testing AI Recovery Monitor POST /api/recovery/scan...")
    res = client.post("/api/recovery/scan")
    assert res.status_code == 200
    scan_res = res.json()["result"]
    print(f"-> Recovery Monitor scanned {scan_res['scanned']} missed visits, processed {scan_res['processed']} new outreach drafts")

    # Test 7: Pending Approvals & Human-in-the-Loop Message Editing
    print("\n[Test 7] Testing Approval Gate GET /api/approvals and POST /api/approvals/{id}/approve...")
    res = client.get("/api/approvals?status=PENDING")
    assert res.status_code == 200
    approvals = res.json()
    print(f"-> Found {len(approvals)} pending approvals awaiting staff authorization")
    assert len(approvals) > 0

    target_apprv = approvals[0]
    print(f"-> Approving outreach for: {target_apprv['patient_name']} (ID: {target_apprv['id']})")
    approve_payload = {
        "status": "APPROVED",
        "edited_message": f"Assalam-o-Alaikum {target_apprv['patient_name'].split()[0]}, Dr. Imran asked us to follow up regarding your clinic visit. Would you like to rebook for tomorrow at 10 AM?",
        "reviewed_by": "Sara, Receptionist"
    }
    res = client.post(f"/api/approvals/{target_apprv['id']}/approve", json=approve_payload)
    assert res.status_code == 200, f"Approval failed: {res.text}"
    print(f"-> Human-in-the-Loop message edited and approved successfully!")

    # Test 8: Simulate Patient Reply - Scenario A: Slot Selection (Happy Path)
    print("\n[Test 8] Testing Simulation Scenario A: Patient selects Slot 1...")
    sim_payload = {
        "appointment_id": target_apprv["appointment_id"],
        "reply_text": "1",
        "scenario": "slot_selection"
    }
    res = client.post("/api/recovery/simulate-reply", json=sim_payload)
    assert res.status_code == 200
    sim_res = res.json()
    print(f"-> Simulation Outcome: {sim_res['status']} | Confirmed Slot: {sim_res.get('confirmed_date')} at {sim_res.get('confirmed_time')}")
    assert sim_res["status"] == "rescheduled"

    # Test 9: Simulate Patient Reply - Scenario B: Clinical Question (Safety Boundary Trigger)
    print("\n[Test 9] Testing Simulation Scenario B: Patient asks medical question (AI Safety Boundary)...")
    sim_clinical_payload = {
        "appointment_id": booked["id"],
        "reply_text": "I am experiencing severe chest pain and fever, can I take Disprin?",
        "scenario": "clinical_question"
    }
    res = client.post("/api/recovery/simulate-reply", json=sim_clinical_payload)
    assert res.status_code == 200
    sim_clin_res = res.json()
    print(f"-> Simulation Outcome: {sim_clin_res['status']} | Safety Action: {sim_clin_res['action_taken']}")
    assert sim_clin_res["status"] == "escalated"

    # Test 10: Audit Logs Verification
    print("\n[Test 10] Testing Live Audit Logs GET /api/audit-logs...")
    res = client.get("/api/audit-logs?limit=10")
    assert res.status_code == 200
    logs = res.json()
    print(f"-> Retrieved {len(logs)} audit logs. Most recent event: [{logs[0]['agent_name']}] {logs[0]['action']}")
    assert len(logs) > 0

    # Test 11: Frontend Serving
    print("\n[Test 11] Testing Frontend Serving GET /...")
    res = client.get("/")
    assert res.status_code == 200
    assert "CareLoop AI" in res.text
    print("-> Frontend successfully served at root /")

    print("\n" + "=" * 60)
    print("ALL 11 INTEGRATION TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
