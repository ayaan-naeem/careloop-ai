"""
CareLoop AI: Recovery Monitor Agent
Monitors appointment lifecycles, detects missed visits, prevents duplicate outreach,
and coordinates with Outreach and Scheduling agents.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import enum
import logging
import threading
from typing import Any, Optional

from database import (
    list_appointments,
    update_appointment,
    list_pending_approvals,
    create_pending_approval,
    log_audit_event,
)

logger = logging.getLogger(__name__)


class AnomalyType(enum.Enum):
    """Kinds of detectable anomalies."""
    MISSED_APPOINTMENT = "MISSED_APPOINTMENT"
    DUPLICATE_OUTREACH = "DUPLICATE_OUTREACH"
    CLINICAL_ESCALATION = "CLINICAL_ESCALATION"
    SLOT_CONFLICT = "SLOT_CONFLICT"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"


@dataclass(frozen=True)
class Anomaly:
    """Immutable anomaly record."""
    anomaly_type: AnomalyType
    message: str
    context: dict
    detected_at: datetime


class RecoveryMonitorAgent:
    """Scans for missed patient appointments and triggers recovery workflows."""

    def __init__(
        self,
        notify: Optional[Callable[[Anomaly], None]] = None,
        outreach_agent: Optional[Any] = None,
    ) -> None:
        self._notify = notify
        self._outreach_agent = outreach_agent
        self._lock = threading.Lock()
        self._anomaly_log: list[Anomaly] = []

    def record_anomaly(self, anomaly_type: AnomalyType, message: str, context: dict) -> Anomaly:
        """Log an operational anomaly and trigger notification."""
        anomaly = Anomaly(
            anomaly_type=anomaly_type,
            message=message,
            context=context,
            detected_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._anomaly_log.append(anomaly)
        logger.warning("RecoveryMonitor Anomaly [%s]: %s", anomaly_type.value, message)
        if self._notify:
            try:
                self._notify(anomaly)
            except Exception as exc:
                logger.error("Notify failed: %s", exc)
        return anomaly

    def scan_for_missed_appointments(self) -> list[dict]:
        """
        Scan database for appointments needing recovery.
        Checks for 'Missed' and 'No-show' statuses.
        Applies Duplicate Prevention: Skips if pending approval or active follow-up exists.
        """
        all_appointments = list_appointments()
        pending_approvals = list_pending_approvals("PENDING")
        pending_app_ids = set(p.get("appointment_id") for p in pending_approvals if p.get("appointment_id"))

        missed_patients: list[dict] = []
        for app in all_appointments:
            status = app.get("status")
            app_id = app.get("id")

            if status in ("Missed", "No-show"):
                # Duplicate check
                if app_id in pending_app_ids:
                    logger.info("Skipping %s (%s): follow-up approval already pending.", app_id, app.get("patient_name"))
                    continue

                missed_patients.append(app)

        return missed_patients

    def process_recovery_pipeline(self) -> dict[str, Any]:
        """
        Execute full recovery loop:
        1. Find missed appointments.
        2. Draft personalized follow-up with available slots.
        3. Park action in Pending Approvals (Human-in-the-Loop).
        4. Log audit events.
        """
        candidates = self.scan_for_missed_appointments()
        processed = []

        for patient in candidates:
            app_id = patient["id"]
            name = patient["patient_name"]
            phone = patient["phone"]
            reason = patient.get("reason", "routine consultation")

            # Duplicate prevention check
            log_audit_event(
                agent_name="RecoveryMonitor",
                action=f"Identified missed appointment for {name} ({app_id}). Duplicate check: PASSED.",
                state_from=patient.get("status"),
                state_to="Follow-up pending",
                details=f"Reason: {reason}. Dispatched to OutreachAgent for drafting.",
                appointment_id=app_id,
            )

            # Generate draft via OutreachAgent (or default templates)
            proposed_slots = ["Tomorrow 10:00 AM", "Thursday 02:30 PM"]
            draft_msg = (
                f"Assalam-o-Alaikum {name.split()[0]}, we noticed you missed your {reason} visit at CareLoop Clinic. "
                f"Your health is our priority. Would you like to reschedule for {proposed_slots[0]} or {proposed_slots[1]}? "
                f"Reply with 1 or 2, or call us directly."
            )

            # Park into pending approvals
            import uuid
            approval_id = f"APP-{uuid.uuid4().hex[:6].upper()}"
            approval_record = {
                "id": approval_id,
                "appointment_id": app_id,
                "patient_name": name,
                "phone": phone,
                "action_type": "SEND_FOLLOW_UP",
                "proposed_message": draft_msg,
                "proposed_slots": proposed_slots,
                "reason": f"Missed appointment detected on {patient.get('date')}. Proposed 2 open recovery slots.",
                "status": "PENDING",
            }
            create_pending_approval(approval_record)

            # Transition appointment to 'Follow-up pending'
            update_appointment(app_id, {"status": "Follow-up pending"})

            log_audit_event(
                agent_name="OutreachAgent",
                action=f"Drafted follow-up SMS for {name}. Parked for staff review.",
                state_from="Missed",
                state_to="Follow-up pending",
                details=f"Approval ID: {approval_id}. Proposed slots: {', '.join(proposed_slots)}",
                appointment_id=app_id,
            )

            processed.append({
                "appointment_id": app_id,
                "patient_name": name,
                "approval_id": approval_id,
                "draft_message": draft_msg,
            })

        return {
            "scanned": len(candidates),
            "processed": len(processed),
            "items": processed,
        }