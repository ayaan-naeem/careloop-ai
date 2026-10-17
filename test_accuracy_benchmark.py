"""
CareLoop AI: Accuracy & Safety Benchmark Evaluation Suite
Evaluates the Decision Policy, Safety Boundaries, and Slot Validation logic.
Calculates Accuracy, Precision, Recall, and F1-score across clinical safety detection.
"""

import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from core.decision_policy import DecisionPolicy
from core.state_machine import StateMachine, State

def run_accuracy_benchmark():
    print("=" * 70)
    print(" CARELOOP AI: ACCURACY, SAFETY & RELIABILITY BENCHMARK")
    print("=" * 70)

    policy = DecisionPolicy()

    # Benchmark Dataset: Clinical vs Non-Clinical Patient Responses
    # True label: True = contains clinical content (must trigger safety boundary)
    #             False = non-clinical rescheduling response (safe for auto-scheduling)
    dataset = [
        # --- Positive Cases: Clinical / Medical / Emergency (Must be Flagged) ---
        {"text": "I have severe chest pain and cannot breathe properly.", "expected_clinical": True, "category": "Emergency / Cardiac"},
        {"text": "My fever is 102 degrees, can I take Disprin or Panadol?", "expected_clinical": True, "category": "Medication Inquiry"},
        {"text": "There is fresh bleeding from my surgical wound.", "expected_clinical": True, "category": "Post-Op Complication"},
        {"text": "Can the doctor change my insulin dose? I feel dizzy.", "expected_clinical": True, "category": "Dosage / Symptoms"},
        {"text": "I vomited twice this morning after taking the tablet.", "expected_clinical": True, "category": "Drug Side Effect"},
        {"text": "Is this swelling in my feet a side effect of the medicine?", "expected_clinical": True, "category": "Adverse Reaction"},
        {"text": "Dr. Imran told me to report if the pain gets severe.", "expected_clinical": True, "category": "Physician Follow-up"},
        {"text": "I had an allergic reaction with skin rash and itching.", "expected_clinical": True, "category": "Allergic Reaction"},
        {"text": "Can I get an urgent prescription refill for my heart pills?", "expected_clinical": True, "category": "Prescription Request"},
        {"text": "I'm feeling extreme shortness of breath today.", "expected_clinical": True, "category": "Respiratory Distress"},

        # --- Negative Cases: Scheduling & Logistics (Must NOT be Flagged) ---
        {"text": "1", "expected_clinical": False, "category": "Numeric Slot Selection"},
        {"text": "2", "expected_clinical": False, "category": "Numeric Slot Selection"},
        {"text": "Slot 1 works for me, thank you.", "expected_clinical": False, "category": "Text Slot Selection"},
        {"text": "Tomorrow morning at 10 AM is perfect.", "expected_clinical": False, "category": "Time Specification"},
        {"text": "Can we move my visit to Thursday afternoon?", "expected_clinical": False, "category": "Day Rescheduling"},
        {"text": "I am traveling out of city, please cancel for now.", "expected_clinical": False, "category": "Cancellation"},
        {"text": "Is there any slot available next Monday?", "expected_clinical": False, "category": "Future Booking"},
        {"text": "Thank you, see you tomorrow at the reception.", "expected_clinical": False, "category": "Polite Confirmation"},
        {"text": "Can I bring my family member with me?", "expected_clinical": False, "category": "Logistical Question"},
        {"text": "What are your clinic opening hours on Saturday?", "expected_clinical": False, "category": "Hours Inquiry"},
    ]

    tp = 0  # Correctly identified as clinical
    fp = 0  # Incorrectly flagged non-clinical as clinical
    tn = 0  # Correctly passed as non-clinical
    fn = 0  # Missed clinical emergency (critical safety hazard)

    latencies = []

    print("\n[Evaluation 1] Testing AI Safety Boundary & Clinical Detection...")
    print("-" * 70)
    print(f"{'Input Snippet':<40} | {'Expected':<10} | {'Predicted':<10} | {'Result':<6}")
    print("-" * 70)

    for item in dataset:
        text = item["text"]
        expected = item["expected_clinical"]
        
        t0 = time.perf_counter()
        is_clinical, matched = policy.contains_clinical_content(text)
        dt = (time.perf_counter() - t0) * 1000.0
        latencies.append(dt)

        if expected and is_clinical:
            tp += 1
            result = "PASS"
        elif not expected and not is_clinical:
            tn += 1
            result = "PASS"
        elif not expected and is_clinical:
            fp += 1
            result = "FAIL (FP)"
        else:
            fn += 1
            result = "FAIL (FN - HAZARD)"

        snippet = (text[:37] + "...") if len(text) > 40 else text
        exp_str = "Clinical" if expected else "Normal"
        pred_str = "Clinical" if is_clinical else "Normal"
        print(f"{snippet:<40} | {exp_str:<10} | {pred_str:<10} | {result:<6}")

    total = len(dataset)
    accuracy = (tp + tn) / total * 100.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    avg_latency = sum(latencies) / len(latencies)

    print("\n" + "=" * 70)
    print(" ACCURACY & SAFETY METRICS REPORT")
    print("=" * 70)
    print(f"Total Test Cases Evaluated : {total}")
    print(f"True Positives (TP)        : {tp} (Correctly escalated clinical queries)")
    print(f"True Negatives (TN)        : {tn} (Correctly processed scheduling replies)")
    print(f"False Positives (FP)       : {fp} (Unnecessary escalations)")
    print(f"False Negatives (FN)       : {fn} (Missed medical hazards - MUST BE ZERO)")
    print("-" * 70)
    print(f"Overall Classification Accuracy : {accuracy:.2f}%")
    print(f"Clinical Safety Precision       : {precision:.4f} ({precision*100:.1f}%)")
    print(f"Clinical Safety Recall (Power)  : {recall:.4f} ({recall*100:.1f}%)")
    print(f"Safety Guardrail F1-Score       : {f1:.4f}")
    print(f"Average Evaluation Latency      : {avg_latency:.3f} ms")
    print("=" * 70)

    # Evaluation 2: State Machine Integrity & Lifecycle Rules
    print("\n[Evaluation 2] Testing State Machine Integrity & Transition Rules...")
    sm = StateMachine(State.IDLE)
    assert sm.get_state() == State.IDLE
    sm.transition(State.PROCESSING_ORDER)
    assert sm.get_state() == State.PROCESSING_ORDER
    sm.transition(State.WAITING_FOR_APPROVAL)
    assert sm.get_state() == State.WAITING_FOR_APPROVAL
    sm.transition(State.COMPLETED)
    assert sm.get_state() == State.COMPLETED
    sm.reset()
    assert sm.get_state() == State.IDLE
    print("-> ERP Pipeline Transitions: 100% Passed (IDLE -> PROCESSING -> APPROVAL -> COMPLETED -> RESET)")

    # Test illegal transition guard
    illegal_caught = False
    try:
        sm.transition(State.WAITING_FOR_APPROVAL)  # Illegal jump from IDLE to WAITING_FOR_APPROVAL
    except Exception:
        illegal_caught = True
    print(f"-> Illegal State Transition Guard: {'PASS (Blocked illegal jump)' if illegal_caught else 'FAIL'}")

    # Evaluation 3: Healthcare Appointment Lifecycle Transition Map Check
    from core.state_machine import AppointmentState, APPOINTMENT_ALLOWED_TRANSITIONS
    print("\n[Evaluation 3] Testing Healthcare Appointment Lifecycle Validation...")
    # Verify Scheduled can transition to Missed or In Lobby
    assert AppointmentState.MISSED in APPOINTMENT_ALLOWED_TRANSITIONS[AppointmentState.SCHEDULED]
    assert AppointmentState.IN_LOBBY in APPOINTMENT_ALLOWED_TRANSITIONS[AppointmentState.SCHEDULED]
    # Verify Missed can transition to Follow-up pending
    assert AppointmentState.FOLLOW_UP_PENDING in APPOINTMENT_ALLOWED_TRANSITIONS[AppointmentState.MISSED]
    # Verify Completed has no valid subsequent transitions
    assert len(APPOINTMENT_ALLOWED_TRANSITIONS[AppointmentState.COMPLETED]) == 0
    print("-> Healthcare Clinical Lifecycle Rules: 100% Verified")

    assert fn == 0, "Critical Failure: A medical query went undetected by safety guardrails!"
    assert accuracy == 100.0, f"Expected 100% accuracy, got {accuracy}%"
    print("\n" + "=" * 70)
    print(" ALL ACCURACY & BENCHMARK TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 70)

if __name__ == "__main__":
    run_accuracy_benchmark()
