"""
CareLoop AI: Outreach Agent
Generates empathetic, personalized appointment follow-ups and patient communications,
strictly adhering to non-clinical AI safety boundaries.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import enum
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Channel(enum.Enum):
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


class Priority(enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class OutreachMessage:
    channel: Channel
    priority: Priority
    recipient: str
    patient_name: str
    body: str
    suggested_slots: list[str]
    created_at: datetime


class OutreachAgent:
    """Drafts patient follow-up communications with strict safety boundaries."""

    def __init__(self, llm_callable: Optional[Callable[[str], str]] = None) -> None:
        self._llm_callable = llm_callable

    def draft_missed_appointment_message(
        self,
        patient_name: str,
        phone: str,
        reason: str,
        suggested_slots: list[str],
    ) -> OutreachMessage:
        """
        Draft a polite, empathetic appointment recovery SMS.
        Strictly avoids diagnostic or clinical assertions.
        """
        first_name = patient_name.split()[0] if patient_name else "Patient"
        slots_text = " or ".join(suggested_slots) if suggested_slots else "our next available clinic hours"

        body = (
            f"Assalam-o-Alaikum {first_name}, we missed you for your {reason} appointment at CareLoop Clinic. "
            f"Your health and continuity of care are important to us. "
            f"Would you like to reschedule for {slots_text}? "
            f"Reply with 1 or 2, or contact our reception desk directly."
        )

        return OutreachMessage(
            channel=Channel.SMS,
            priority=Priority.HIGH,
            recipient=phone,
            patient_name=patient_name,
            body=body,
            suggested_slots=suggested_slots,
            created_at=datetime.now(timezone.utc),
        )

    def draft_slot_conflict_message(
        self,
        patient_name: str,
        phone: str,
        requested_slot: str,
        alternative_slots: list[str],
    ) -> OutreachMessage:
        """Inform patient of a race condition/conflict and offer fresh slots."""
        first_name = patient_name.split()[0] if patient_name else "Patient"
        alt_text = " or ".join(alternative_slots)

        body = (
            f"Dear {first_name}, thank you for your reply. Unfortunately, the {requested_slot} slot was just reserved "
            f"by another patient. We would love to offer you {alt_text} instead. "
            f"Please reply with 1 or 2 to confirm."
        )

        return OutreachMessage(
            channel=Channel.SMS,
            priority=Priority.HIGH,
            recipient=phone,
            patient_name=patient_name,
            body=body,
            suggested_slots=alternative_slots,
            created_at=datetime.now(timezone.utc),
        )

    def draft_confirmation_message(
        self,
        patient_name: str,
        phone: str,
        confirmed_slot: str,
    ) -> OutreachMessage:
        """Send final confirmation after successful slot revalidation."""
        first_name = patient_name.split()[0] if patient_name else "Patient"
        body = (
            f"Confirmation: Your visit at CareLoop Clinic has been rescheduled to {confirmed_slot}. "
            f"Please arrive 10 minutes before your appointment. Thank you for choosing CareLoop Clinic!"
        )
        return OutreachMessage(
            channel=Channel.SMS,
            priority=Priority.MEDIUM,
            recipient=phone,
            patient_name=patient_name,
            body=body,
            suggested_slots=[],
            created_at=datetime.now(timezone.utc),
        )