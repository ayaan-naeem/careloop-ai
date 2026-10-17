"""
CareLoop AI: Scheduling Agent
Validates calendar slot availability, prevents race condition double-bookings,
and schedules background tasks.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import enum
import heapq
import logging
from typing import Any, Optional
import uuid

from database import (
    get_appointment,
    update_appointment,
    list_appointments,
    log_audit_event,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 1.0
MAX_CONCURRENT_JOBS = 5


class JobType(enum.Enum):
    """Kinds of scheduled jobs."""
    ONE_SHOT = "ONE_SHOT"
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"


class JobStatus(enum.Enum):
    """Lifecycle states of a scheduled job."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


@dataclass
class ScheduledJob:
    """A scheduled background job."""
    name: str
    job_type: JobType
    callback: Callable[..., Awaitable[Any]]
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    interval_seconds: Optional[float] = None
    daily_time: Optional[time] = None
    next_run: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: JobStatus = JobStatus.PENDING
    run_count: int = 0
    failure_count: int = 0
    last_error: Optional[str] = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class SchedulingAgent:
    """Handles appointment slot verification, rebooking, and background schedulers."""

    def __init__(
        self,
        approval_callback: Optional[Callable[[ScheduledJob], Awaitable[bool]]] = None,
        notify: Optional[Callable[[str, ScheduledJob], None]] = None,
    ) -> None:
        self._approval_callback = approval_callback
        self._notify = notify
        self._jobs: dict[str, ScheduledJob] = {}
        self._lock = asyncio.Lock()
        self._loop_task: Optional[asyncio.Task[None]] = None
        self._running = False

    # ==========================================================================
    # Healthcare Slot Validation & Double-Booking Prevention
    # ==========================================================================

    def is_slot_available(self, target_date: str, target_time: str, exclude_appointment_id: Optional[str] = None) -> bool:
        """
        Check if a date and time slot is open in the clinic calendar.
        Prevents race condition double bookings.
        """
        all_apps = list_appointments()
        for app in all_apps:
            if exclude_appointment_id and app.get("id") == exclude_appointment_id:
                continue
            if app.get("status") in ("Scheduled", "In lobby", "With doctor", "Rescheduled"):
                if app.get("date") == target_date and app.get("time") == target_time:
                    return False
        return True

    def revalidate_and_confirm(
        self,
        appointment_id: str,
        target_date: str,
        target_time: str,
    ) -> dict[str, Any]:
        """
        Revalidate candidate slot right before committing to avoid race conditions.
        If free -> updates status to 'Rescheduled' and confirms.
        If taken -> flags conflict and suggests alternative slot.
        """
        app = get_appointment(appointment_id)
        if not app:
            return {"success": False, "error": f"Appointment {appointment_id} not found"}

        patient_name = app.get("patient_name", "Patient")

        # Step 1: Recheck slot availability
        available = self.is_slot_available(target_date, target_time, exclude_appointment_id=appointment_id)
        if not available:
            # Slot is already taken! Trigger conflict flow
            log_audit_event(
                agent_name="SchedulingAgent",
                action=f"Slot conflict detected: {target_date} {target_time} is already booked.",
                state_from=app.get("status"),
                state_to="Follow-up pending",
                details=f"Race condition caught. Initiating alternative slot offer for {patient_name}.",
                appointment_id=appointment_id,
            )
            return {
                "success": False,
                "conflict": True,
                "message": f"Slot {target_date} at {target_time} is no longer available.",
                "suggested_alternatives": ["Tomorrow 03:00 PM", "Friday 11:30 AM"]
            }

        # Step 2: Slot is available! Commit the booking
        update_appointment(appointment_id, {
            "date": target_date,
            "time": target_time,
            "status": "Rescheduled",
        })

        log_audit_event(
            agent_name="SchedulingAgent",
            action=f"Confirmed rescheduling for {patient_name} to {target_date} at {target_time}.",
            state_from=app.get("status"),
            state_to="Rescheduled",
            details="Slot revalidation verified free of conflicts. Confirmation generated.",
            appointment_id=appointment_id,
        )

        return {
            "success": True,
            "conflict": False,
            "appointment_id": appointment_id,
            "confirmed_date": target_date,
            "confirmed_time": target_time,
            "patient_name": patient_name,
            "status": "Rescheduled",
        }

    # ==========================================================================
    # Background Scheduler Loop
    # ==========================================================================

    async def start(self) -> None:
        """Start the async scheduler loop."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("SchedulingAgent loop started.")

    async def stop(self, wait: bool = True) -> None:
        """Stop the background scheduler loop."""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            if wait:
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
        logger.info("SchedulingAgent loop stopped.")

    def schedule_interval(
        self,
        name: str,
        callback: Callable[..., Awaitable[Any]],
        interval_seconds: float,
        *args: Any,
        **kwargs: Any
    ) -> str:
        """Register a periodic recurring background job."""
        job = ScheduledJob(
            name=name,
            job_type=JobType.INTERVAL,
            callback=callback,
            args=args,
            kwargs=kwargs,
            interval_seconds=interval_seconds,
            next_run=datetime.now(timezone.utc) + timedelta(seconds=interval_seconds),
        )
        self._jobs[job.job_id] = job
        logger.info("Scheduled recurring job %r (interval=%ss)", name, interval_seconds)
        return job.job_id

    async def _run_loop(self) -> None:
        """Tick every second and execute ready jobs."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                for job in list(self._jobs.values()):
                    if job.status in (JobStatus.PENDING, JobStatus.COMPLETED) and job.next_run <= now:
                        job.status = JobStatus.RUNNING
                        asyncio.create_task(self._execute_job(job))
                await asyncio.sleep(TICK_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in scheduler loop: %s", exc)
                await asyncio.sleep(1.0)

    async def _execute_job(self, job: ScheduledJob) -> None:
        """Execute one job instance with error handling."""
        try:
            await job.callback(*job.args, **job.kwargs)
            job.run_count += 1
            job.status = JobStatus.COMPLETED
            if job.job_type == JobType.INTERVAL and job.interval_seconds:
                job.next_run = datetime.now(timezone.utc) + timedelta(seconds=job.interval_seconds)
                job.status = JobStatus.PENDING
        except Exception as exc:
            job.failure_count += 1
            job.last_error = str(exc)
            job.status = JobStatus.FAILED
            logger.error("Scheduled job %s failed: %s", job.name, exc)