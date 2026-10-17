"""
CareLoop AI: State Machine
Governs appointment recovery lifecycles and ERP task transitions with strict rule enforcement.
"""

from datetime import datetime, timezone
import enum
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class AppointmentState(enum.Enum):
    """Lifecycle states for patient clinic recovery."""
    SCHEDULED = "Scheduled"
    MISSED = "Missed"
    FOLLOW_UP_PENDING = "Follow-up pending"
    SLOT_VERIFYING = "Slot verifying"
    RESCHEDULED = "Rescheduled"
    ESCALATED = "Escalated"
    COMPLETED = "Completed"
    NO_SHOW = "No-show"
    IN_LOBBY = "In lobby"
    WITH_DOCTOR = "With doctor"


class State(enum.Enum):
    """Workflow states for the ERP agent pipeline."""
    IDLE = "IDLE"
    PROCESSING_ORDER = "PROCESSING_ORDER"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"


# Allowed transitions for ERP pipeline
ERP_ALLOWED_TRANSITIONS: dict[State, frozenset[State]] = {
    State.IDLE: frozenset({State.PROCESSING_ORDER}),
    State.PROCESSING_ORDER: frozenset({State.WAITING_FOR_APPROVAL, State.COMPLETED, State.IDLE}),
    State.WAITING_FOR_APPROVAL: frozenset({State.COMPLETED, State.PROCESSING_ORDER, State.IDLE}),
    State.COMPLETED: frozenset({State.IDLE}),
}

# Allowed transitions for Clinic Appointment Recovery
APPOINTMENT_ALLOWED_TRANSITIONS: dict[AppointmentState, frozenset[AppointmentState]] = {
    AppointmentState.SCHEDULED: frozenset({
        AppointmentState.IN_LOBBY,
        AppointmentState.MISSED,
        AppointmentState.NO_SHOW,
        AppointmentState.COMPLETED
    }),
    AppointmentState.IN_LOBBY: frozenset({
        AppointmentState.WITH_DOCTOR,
        AppointmentState.NO_SHOW
    }),
    AppointmentState.WITH_DOCTOR: frozenset({
        AppointmentState.COMPLETED
    }),
    AppointmentState.MISSED: frozenset({
        AppointmentState.FOLLOW_UP_PENDING,
        AppointmentState.ESCALATED,
        AppointmentState.RESCHEDULED
    }),
    AppointmentState.NO_SHOW: frozenset({
        AppointmentState.FOLLOW_UP_PENDING,
        AppointmentState.ESCALATED,
        AppointmentState.RESCHEDULED
    }),
    AppointmentState.FOLLOW_UP_PENDING: frozenset({
        AppointmentState.SLOT_VERIFYING,
        AppointmentState.ESCALATED,
        AppointmentState.RESCHEDULED,
        AppointmentState.MISSED
    }),
    AppointmentState.SLOT_VERIFYING: frozenset({
        AppointmentState.RESCHEDULED,
        AppointmentState.FOLLOW_UP_PENDING,
        AppointmentState.ESCALATED
    }),
    AppointmentState.RESCHEDULED: frozenset({
        AppointmentState.SCHEDULED,
        AppointmentState.IN_LOBBY,
        AppointmentState.COMPLETED
    }),
    AppointmentState.ESCALATED: frozenset({
        AppointmentState.SCHEDULED,
        AppointmentState.COMPLETED,
        AppointmentState.RESCHEDULED
    }),
    AppointmentState.COMPLETED: frozenset(),
}


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not allowed."""
    def __init__(self, current: Any, requested: Any) -> None:
        super().__init__(f"Invalid transition from {current!r} to {requested!r}")
        self.current = current
        self.requested = requested


class StateMachine:
    """Thread-safe ERP workflow state machine."""

    def __init__(self, initial_state: State = State.IDLE) -> None:
        if not isinstance(initial_state, State):
            raise TypeError(f"initial_state must be a State, got {initial_state!r}")
        self._state: State = initial_state
        self._lock = threading.Lock()
        self._history: list[tuple[State, State, datetime]] = []

    def get_state(self) -> State:
        with self._lock:
            return self._state

    def can_transition(self, new_state: State) -> bool:
        if not isinstance(new_state, State):
            return False
        with self._lock:
            return new_state in ERP_ALLOWED_TRANSITIONS.get(self._state, frozenset())

    def transition(self, new_state: State) -> State:
        with self._lock:
            allowed = ERP_ALLOWED_TRANSITIONS.get(self._state, frozenset())
            if new_state not in allowed:
                raise InvalidTransitionError(self._state, new_state)
            old_state = self._state
            self._state = new_state
            self._history.append((old_state, new_state, datetime.now(timezone.utc)))
            logger.info("Transition %s -> %s", old_state, new_state)
            return self._state

    def reset(self) -> None:
        with self._lock:
            if self._state is State.IDLE:
                return
            old_state = self._state
            self._state = State.IDLE
            self._history.append((old_state, State.IDLE, datetime.now(timezone.utc)))
            logger.info("Reset %s -> %s", old_state, State.IDLE)

    @property
    def history(self) -> tuple[tuple[State, State, datetime], ...]:
        with self._lock:
            return tuple(self._history)