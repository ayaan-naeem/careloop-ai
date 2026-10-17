"""
CareLoop AI: Main FastAPI Application & Agent Coordination Hub
Connects Recovery Monitor, Outreach Agent, and Scheduling Agent with Human-in-the-Loop oversight.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sys
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
import uvicorn

# Core and Database
import database
from database import (
    init_db,
    get_db_status,
    get_system_stats,
    list_appointments,
    get_appointment,
    create_appointment,
    update_appointment,
    list_pending_approvals,
    get_pending_approval,
    create_pending_approval,
    resolve_pending_approval,
    log_audit_event,
    list_audit_logs,
    seed_synthetic_data,
    get_inventory_status,
    update_stock,
    add_new_item,
)
from models import (
    AppointmentCreate,
    AppointmentUpdate,
    ApprovalResolveRequest,
    SimulateReplyRequest,
    InventoryItem,
    OrderRequest,
)
from core.state_machine import StateMachine, State, InvalidTransitionError
from core.decision_policy import DecisionPolicy
from agents.recovery_moniter import RecoveryMonitorAgent
from agents.outreach_agent import OutreachAgent
from agents.scheduling_agent import SchedulingAgent
from tools.mcp_client import MCPClient, MCPClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FASTN_API_KEY = os.getenv("FASTN_API_KEY", "")
FASTN_SPACE_ID = os.getenv("FASTN_SPACE_ID", "")
MCP_COMMAND = "fastn-mcp-server"
MCP_ARGS = ["--api_key", FASTN_API_KEY, "--space_id", FASTN_SPACE_ID] if FASTN_API_KEY and FASTN_SPACE_ID else []

LOW_STOCK_CHECK_SECONDS = int(os.getenv("LOW_STOCK_CHECK_SECONDS", "300"))
RECOVERY_SCAN_SECONDS = int(os.getenv("RECOVERY_SCAN_SECONDS", "300"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


class AppContext:
    """Runtime context holding state machines, policies, and active agents."""

    def __init__(self) -> None:
        self.state_machine = StateMachine()
        self.decision_policy = DecisionPolicy()
        self.outreach = OutreachAgent()
        self.recovery = RecoveryMonitorAgent()
        self.scheduler = SchedulingAgent()
        self.pending: dict[str, dict] = {}
        self.lock = asyncio.Lock()
        self.mcp_client: Optional[MCPClient] = None
        self.mcp_tools: list[dict] = []


async def _init_context() -> AppContext:
    """Initialize database, seed sample data, connect MCP if configured, and start scheduler."""
    init_db()
    seed_synthetic_data(force=False)

    ctx = AppContext()

    # Optional Fastn MCP Client connection
    if FASTN_API_KEY and FASTN_SPACE_ID:
        try:
            client = MCPClient(MCP_COMMAND, MCP_ARGS)
            await client.__aenter__()
            ctx.mcp_client = client
            ctx.mcp_tools = await ctx.mcp_client.list_tools()
            logger.info("Connected to Fastn MCP with %d tools", len(ctx.mcp_tools))
        except Exception as exc:
            logger.warning("Fastn MCP connection not established (%s). Running in local mode.", exc)
            ctx.mcp_client = None
            ctx.mcp_tools = []

    # Start background scheduler
    await ctx.scheduler.start()

    # Periodic background scan for missed appointments
    async def _periodic_recovery_scan() -> None:
        try:
            result = ctx.recovery.process_recovery_pipeline()
            if result.get("processed", 0) > 0:
                logger.info("Periodic recovery scan processed %d missed visits", result["processed"])
        except Exception as scan_err:
            logger.error("Periodic recovery scan error: %s", scan_err)

    ctx.scheduler.schedule_interval("periodic_recovery_scan", _periodic_recovery_scan, float(RECOVERY_SCAN_SECONDS))

    return ctx


async def _shutdown_context(ctx: AppContext) -> None:
    """Gracefully stop background schedulers and MCP clients."""
    try:
        await ctx.scheduler.stop(wait=True)
    except Exception as exc:
        logger.error("Error stopping scheduler: %s", exc)

    if ctx.mcp_client is not None:
        try:
            await ctx.mcp_client.__aexit__(None, None, None)
        except Exception as exc:
            logger.error("Error closing MCP client: %s", exc)
        ctx.mcp_client = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """FastAPI lifespan holding the runtime AppContext."""
    ctx = await _init_context()
    app.state.ctx = ctx
    yield
    await _shutdown_context(ctx)


app = FastAPI(
    title="CareLoop AI Clinic Portal & Recovery System",
    description="Agentic Healthcare Operations platform with Human-in-the-Loop approval workflows and Supabase Cloud integration.",
    version="2.0.0",
    lifespan=lifespan,
)

# Enable CORS for seamless interaction with local and cloud frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_global_ctx: Optional[AppContext] = None


def _ctx(request: Optional[Request] = None) -> AppContext:
    """Helper to retrieve AppContext from request or fallback global."""
    global _global_ctx
    if request is not None and hasattr(request.app.state, "ctx"):
        return request.app.state.ctx
    if _global_ctx is None:
        _global_ctx = AppContext()
    return _global_ctx


# ==============================================================================
# Frontend Root & Health Endpoints
# ==============================================================================

@app.get("/")
@app.get("/portal")
async def serve_frontend() -> Any:
    """Serve the clinic portal web interface."""
    if FRONTEND_PATH.exists():
        return FileResponse(FRONTEND_PATH)
    return {"message": "CareLoop AI API running. Frontend file not found at " + str(FRONTEND_PATH)}


@app.get("/health")
async def health(request: Request) -> dict:
    """Return system health, database mode, and pending approval counts."""
    ctx = _ctx(request)
    db_info = get_db_status()
    approvals = list_pending_approvals("PENDING")
    return {
        "status": "healthy",
        "database": db_info,
        "erp_state": ctx.state_machine.get_state().value,
        "pending_approvals_count": len(approvals),
        "mcp_connected": ctx.mcp_client is not None and len(ctx.mcp_tools) > 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/stats")
async def get_stats() -> dict:
    """Get live clinic metrics and recovery stats."""
    return get_system_stats()


# ==============================================================================
# Appointments Endpoints (Patient Booking & Reception Desk)
# ==============================================================================

@app.get("/api/appointments")
async def get_appointments(
    status: Optional[str] = Query(None, description="Filter by status: Scheduled, In lobby, With doctor, Completed, Missed, No-show, Rescheduled, Escalated"),
    search: Optional[str] = Query(None, description="Search by patient name or phone number"),
) -> list[dict]:
    """Retrieve appointments list."""
    return list_appointments(status=status, search=search)


@app.post("/api/appointments")
async def book_appointment(appointment: AppointmentCreate) -> dict:
    """Book a new clinic appointment from patient portal."""
    try:
        data = appointment.model_dump()
        created = create_appointment(data)
        log_audit_event(
            agent_name="PatientPortal",
            action=f"New appointment booked for {created['patient_name']} on {created['date']} at {created['time']}.",
            state_from="None",
            state_to="Scheduled",
            details=f"Reason: {created['reason']}",
            appointment_id=created["id"],
        )
        return {"status": "success", "appointment": created}
    except Exception as exc:
        logger.error("Failed to book appointment: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create appointment")


@app.patch("/api/appointments/{appointment_id}/status")
async def update_status(appointment_id: str, payload: AppointmentUpdate) -> dict:
    """Update appointment status (e.g. check-in to lobby, send to doctor, mark completed, or mark missed)."""
    app = get_appointment(appointment_id)
    if not app:
        raise HTTPException(status_code=404, detail="Appointment not found")

    old_status = app.get("status")
    updates = {}
    if payload.status:
        updates["status"] = payload.status
    if payload.date:
        updates["date"] = payload.date
    if payload.time:
        updates["time"] = payload.time
    if payload.notes:
        updates["notes"] = payload.notes

    updated = update_appointment(appointment_id, updates)

    log_audit_event(
        agent_name="ClinicStaff",
        action=f"Updated status of {app['patient_name']} from '{old_status}' to '{payload.status}'.",
        state_from=old_status,
        state_to=payload.status,
        details=f"Notes: {payload.notes or 'None'}",
        appointment_id=appointment_id,
    )
    return {"status": "success", "appointment": updated}


# ==============================================================================
# Human-in-the-Loop Approval Gate Endpoints
# ==============================================================================

@app.get("/api/approvals")
async def get_approvals(status: str = Query("PENDING", description="Filter approvals: PENDING, APPROVED, REJECTED, All")) -> list[dict]:
    """List pending actions awaiting staff review."""
    return list_pending_approvals(status=status)


@app.post("/api/approvals/{approval_id}/approve")
async def approve_action(approval_id: str, payload: Optional[ApprovalResolveRequest] = None) -> dict:
    """
    Approve an outreach message (Hero Moment in Competition).
    Allows clinic staff to edit the message before it is sent!
    """
    approval = get_pending_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    reviewed_by = payload.reviewed_by if payload and payload.reviewed_by else "Sara, Receptionist"
    edited_msg = payload.edited_message if payload and payload.edited_message else None

    # Mark as approved in database
    resolved = resolve_pending_approval(approval_id, "APPROVED", reviewed_by=reviewed_by)

    app_id = approval.get("appointment_id")
    final_message = edited_msg if edited_msg else approval.get("proposed_message")

    log_audit_event(
        agent_name="ClinicStaff",
        action=f"Staff approved follow-up outreach for {approval.get('patient_name')}. Message dispatched via SMS/WhatsApp.",
        state_from="Follow-up pending",
        state_to="Follow-up pending",
        details=f"Reviewed by: {reviewed_by}. Edited: {'Yes' if edited_msg else 'No'}. Dispatched message: {final_message!r}",
        appointment_id=app_id,
    )

    return {
        "status": "approved",
        "approval_id": approval_id,
        "message_dispatched": final_message,
        "reviewed_by": reviewed_by,
        "appointment_id": app_id,
    }


@app.post("/api/approvals/{approval_id}/reject")
async def reject_action(approval_id: str, payload: Optional[ApprovalResolveRequest] = None) -> dict:
    """Reject or cancel an outreach message."""
    approval = get_pending_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    reviewed_by = payload.reviewed_by if payload and payload.reviewed_by else "Staff"
    resolved = resolve_pending_approval(approval_id, "REJECTED", reviewed_by=reviewed_by)

    app_id = approval.get("appointment_id")
    log_audit_event(
        agent_name="ClinicStaff",
        action=f"Staff rejected proposed outreach for {approval.get('patient_name')}.",
        state_from="Follow-up pending",
        state_to="Missed",
        details=f"Reviewed by: {reviewed_by}. Action cancelled.",
        appointment_id=app_id,
    )

    # Return appointment status to Missed
    if app_id:
        update_appointment(app_id, {"status": "Missed"})

    return {"status": "rejected", "approval_id": approval_id}


# ==============================================================================
# CareLoop AI Multi-Agent Recovery Engine & Simulator
# ==============================================================================

@app.post("/api/recovery/scan")
async def trigger_recovery_scan(request: Request) -> dict:
    """
    Manually trigger Recovery Monitor Agent scan.
    Identifies all missed appointments, performs duplicate check, and generates approval drafts.
    """
    ctx = _ctx(request)
    result = ctx.recovery.process_recovery_pipeline()
    return {"status": "success", "result": result}


@app.post("/api/recovery/simulate-reply")
async def simulate_patient_reply(request: Request, payload: SimulateReplyRequest) -> dict:
    """
    Simulate patient response for competition live demonstrations:
    - Slot Selection: Revalidates slot and confirms rebooking automatically.
    - Clinical Question: Triggers AI safety boundary and escalates to Doctor queue.
    - Slot Conflict: Simulates race condition and offers alternate slot.
    """
    ctx = _ctx(request)
    app = get_appointment(payload.appointment_id)
    if not app:
        raise HTTPException(status_code=404, detail="Appointment not found")

    patient_name = app.get("patient_name", "Patient")
    evaluation = ctx.decision_policy.evaluate_patient_reply(payload.reply_text)

    # Handle Scenario: Clinical Safety Trigger
    if evaluation.get("is_clinical") or payload.scenario == "clinical_question":
        update_appointment(payload.appointment_id, {"status": "Escalated"})
        log_audit_event(
            agent_name="DecisionPolicy",
            action=f"AI Safety Boundary Triggered for {patient_name}. Direct medical advice blocked.",
            state_from=app.get("status"),
            state_to="Escalated",
            details=f"Patient text: {payload.reply_text!r}. Escalated to Doctor consultation queue.",
            appointment_id=payload.appointment_id,
        )
        return {
            "status": "escalated",
            "appointment_id": payload.appointment_id,
            "patient_name": patient_name,
            "reason": evaluation.get("reason", "Clinical question requires human physician review."),
            "action_taken": "Flagged and transferred directly to Doctor's Queue.",
        }

    # Handle Scenario: Slot Conflict (Race Condition)
    if payload.scenario == "slot_conflict":
        log_audit_event(
            agent_name="SchedulingAgent",
            action=f"Race condition detected: Requested slot was claimed concurrently.",
            state_from=app.get("status"),
            state_to="Follow-up pending",
            details="Scheduling Agent prevented double-booking. Alternate slots proposed.",
            appointment_id=payload.appointment_id,
        )
        return {
            "status": "slot_conflict",
            "appointment_id": payload.appointment_id,
            "message": "That slot was just booked by another patient. We have offered alternative options.",
            "alternatives": ["Friday 10:00 AM", "Saturday 02:00 PM"],
        }

    # Handle Scenario: Slot Selection (Happy Path)
    # Target new date: tomorrow or in 2 days
    from datetime import date, timedelta
    target_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    target_time = "10:00"

    sched_agent = ctx.scheduler
    rebook_res = sched_agent.revalidate_and_confirm(payload.appointment_id, target_date, target_time)

    return {
        "status": "rescheduled" if rebook_res.get("success") else "conflict",
        "appointment_id": payload.appointment_id,
        "patient_name": patient_name,
        "confirmed_date": target_date,
        "confirmed_time": target_time,
        "details": rebook_res,
    }


@app.get("/api/audit-logs")
async def get_audit_logs(limit: int = Query(50, description="Max logs to return")) -> list[dict]:
    """Retrieve audit events and agent decision traces."""
    return list_audit_logs(limit=limit)


@app.post("/api/seed")
async def seed_data() -> dict:
    """Reset and seed the 30 synthetic appointments for the competition pilot."""
    seeded = seed_synthetic_data(force=True)
    return {"status": "success", "count": len(seeded), "message": "Successfully seeded 30 pilot appointments."}


# ==============================================================================
# ERP Tools & Catalog Compatibility Endpoints
# ==============================================================================

@app.get("/inventory")
async def get_inventory() -> list[dict]:
    """Return clinic medicine and inventory rows."""
    return get_inventory_status()


@app.post("/inventory")
async def create_inventory_item(item: InventoryItem) -> dict:
    """Add a new inventory item."""
    row_id = add_new_item(item.item_name, item.quantity, item.price)
    return {"status": "success", "id": row_id, "item_name": item.item_name}


@app.post("/orders")
async def update_inventory_order(order: OrderRequest) -> dict:
    """Adjust stock level."""
    success = update_stock(order.item_name, order.quantity_to_add_or_remove)
    if not success:
        raise HTTPException(status_code=400, detail="Stock adjustment failed or item not found")
    return {"status": "success", "item_name": order.item_name, "change": order.quantity_to_add_or_remove}


@app.get("/tools")
async def list_tools(request: Request) -> list[dict]:
    """List discovered Fastn MCP tools."""
    ctx = _ctx(request)
    return ctx.mcp_tools


# ==============================================================================
# Main Entry Point
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CareLoop AI Server")
    parser.add_argument("--port", type=int, default=PORT, help="Port to run server on")
    parser.add_argument("--host", type=str, default=HOST, help="Host to bind to")
    args = parser.parse_args()

    print(f"Starting CareLoop AI on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)