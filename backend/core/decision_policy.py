"""
CareLoop AI: Decision Policy & AI Safety Boundary
Enforces strict boundaries between automated AI actions and required human oversight.
"""

import enum
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ORDER_VALUE_THRESHOLD = 1000.0
STOCK_CHANGE_THRESHOLD = 100
PRICE_CHANGE_PERCENT_THRESHOLD = 20.0
MAX_UNANSWERED_OUTREACH_ATTEMPTS = 3

# High-risk clinical keywords that AI is forbidden to interpret or prescribe for
CLINICAL_SAFETY_KEYWORDS = [
    "pain", "severe", "chest pain", "fever", "bleeding", "emergency", "dose",
    "dosage", "prescription", "medicine", "pill", "injection", "vomiting",
    "dizziness", "shortness of breath", "allergic", "swelling", "infection",
    "heart", "surgery", "dr", "doctor", "tablet", "syrup", "side effect"
]


class ActionType(enum.Enum):
    """Supported ERP and Clinical action types."""
    ADD_ITEM = "ADD_ITEM"
    UPDATE_STOCK = "UPDATE_STOCK"
    DELETE_ITEM = "DELETE_ITEM"
    PLACE_ORDER = "PLACE_ORDER"
    CHANGE_PRICE = "CHANGE_PRICE"
    SEND_FOLLOW_UP = "SEND_FOLLOW_UP"
    CONFIRM_RESCHEDULE = "CONFIRM_RESCHEDULE"
    ESCALATE_TO_STAFF = "ESCALATE_TO_STAFF"


class DecisionPolicy:
    """Decide whether an action can execute automatically or requires human approval."""

    def __init__(
        self,
        order_value_threshold: float = ORDER_VALUE_THRESHOLD,
        stock_change_threshold: int = STOCK_CHANGE_THRESHOLD,
        price_change_percent_threshold: float = PRICE_CHANGE_PERCENT_THRESHOLD,
    ) -> None:
        self._order_value_threshold = order_value_threshold
        self._stock_change_threshold = stock_change_threshold
        self._price_change_percent_threshold = price_change_percent_threshold

    # ==========================================================================
    # Healthcare Clinical Safety Evaluation
    # ==========================================================================

    def contains_clinical_content(self, text: str) -> tuple[bool, list[str]]:
        """
        Check if text mentions medical symptoms, medications, or emergencies.
        Returns (has_clinical, matched_keywords).
        """
        if not text:
            return False, []
        text_lower = text.lower()
        matched = []
        for kw in CLINICAL_SAFETY_KEYWORDS:
            # Match whole word or exact phrase
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, text_lower):
                matched.append(kw)
        return len(matched) > 0, matched

    def evaluate_patient_reply(self, reply_text: str, current_attempts: int = 1) -> dict[str, Any]:
        """
        Evaluate a patient's SMS or WhatsApp response against clinical boundaries.
        Returns:
            - next_action: "AUTO_RESCHEDULE" | "OFFER_ALTERNATE_SLOT" | "ESCALATE_TO_STAFF"
            - reason: Explanation for staff
            - requires_human: bool
            - is_clinical: bool
        """
        # Rule 1: Check for clinical questions / symptoms
        is_clinical, matched = self.contains_clinical_content(reply_text)
        if is_clinical:
            return {
                "next_action": "ESCALATE_TO_STAFF",
                "requires_human": True,
                "is_clinical": True,
                "reason": f"Safety boundary triggered: Patient mentioned clinical term(s): {', '.join(matched)}. AI cannot provide medical advice.",
                "suggested_reply": None
            }

        # Rule 2: Check for outreach attempts threshold
        if current_attempts >= MAX_UNANSWERED_OUTREACH_ATTEMPTS:
            return {
                "next_action": "ESCALATE_TO_STAFF",
                "requires_human": True,
                "is_clinical": False,
                "reason": f"Patient has not confirmed after {current_attempts} attempts. Escalating to clinic phone desk.",
                "suggested_reply": None
            }

        # Rule 3: Check for slot selection (e.g. '1', '2', 'slot 1', 'tomorrow', '10am')
        clean = reply_text.strip().lower()
        slot_selected = None
        if clean in ("1", "first", "option 1", "slot 1", "morning") or "tomorrow" in clean:
            slot_selected = 0
        elif clean in ("2", "second", "option 2", "slot 2", "afternoon") or "thursday" in clean or "next" in clean:
            slot_selected = 1

        if slot_selected is not None:
            return {
                "next_action": "AUTO_RESCHEDULE",
                "requires_human": False,
                "is_clinical": False,
                "slot_index": slot_selected,
                "reason": f"Patient selected proposed slot {slot_selected + 1}. Proceeding to slot revalidation."
            }

        # Rule 4: Ambiguous reply -> route to staff
        return {
            "next_action": "ESCALATE_TO_STAFF",
            "requires_human": True,
            "is_clinical": False,
            "reason": f"Unrecognized patient response: {reply_text!r}. Routed to receptionist for clarification."
        }

    # ==========================================================================
    # ERP & Inventory Policy
    # ==========================================================================

    def should_require_approval(self, action_type: str, details: dict) -> bool:
        """Return True if action exceeds risk thresholds."""
        normalized = action_type.strip().upper() if isinstance(action_type, str) else ""

        # Clinic follow-up messages ALWAYS require initial staff approval (Hero moment)
        if normalized in ("SEND_FOLLOW_UP", "OUTREACH"):
            return True

        if normalized == "ESCALATE_TO_STAFF":
            return True

        if normalized == "DELETE_ITEM":
            return True

        if normalized in ("UPDATE_STOCK", "PLACE_ORDER"):
            qty = abs(details.get("quantity_to_add_or_remove", details.get("quantity", 0)))
            if qty > self._stock_change_threshold:
                return True

        if normalized == "ADD_ITEM":
            qty = details.get("quantity", 0)
            price = details.get("price", 0.0)
            if (qty * price) > self._order_value_threshold:
                return True

        return False

    def explain(self, action_type: str, details: dict) -> str:
        """Provide transparent human-readable explanation for decision."""
        normalized = action_type.strip().upper() if isinstance(action_type, str) else ""
        if normalized == "SEND_FOLLOW_UP":
            return f"Patient outreach message drafted for {details.get('patient_name', 'patient')}. Staff review required before SMS dispatch."
        if normalized == "DELETE_ITEM":
            return "Deleting catalog items is destructive and requires human authorization."
        if normalized in ("UPDATE_STOCK", "PLACE_ORDER"):
            qty = abs(details.get("quantity_to_add_or_remove", details.get("quantity", 0)))
            if qty > self._stock_change_threshold:
                return f"Stock change of {qty} exceeds safety threshold ({self._stock_change_threshold})."
        return "Standard approval required based on clinic safety policy."