"""
CareLoop AI: Database Access Layer
Supports Supabase Cloud (PostgreSQL) with seamless local SQLite fallback.
"""

from datetime import datetime, timezone, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Optional
# Load environment variables from backend/.env or root .env
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    # Zero-dependency fallback: parse .env manually if python-dotenv is not installed
    for candidate_dir in [Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent]:
        env_candidate = candidate_dir / ".env"
        if env_candidate.exists():
            try:
                with open(env_candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "careloop.db"
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

_supabase_client = None
_use_supabase = False

if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client, Client
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        _use_supabase = True
        logger.info("Supabase client initialized successfully with URL: %s", SUPABASE_URL)
    except Exception as exc:
        logger.warning("Failed to initialize Supabase client (%s). Falling back to local SQLite.", exc)
        _use_supabase = False
else:
    logger.info("SUPABASE_URL or SUPABASE_KEY not provided. Running in local SQLite mode.")


def get_db_status() -> dict[str, Any]:
    """Return database connection status and mode."""
    return {
        "mode": "supabase" if _use_supabase else "sqlite_local",
        "supabase_connected": _use_supabase,
        "supabase_url": SUPABASE_URL if _use_supabase else None,
        "sqlite_path": str(DB_PATH) if not _use_supabase else None,
    }


def _get_sqlite_conn() -> sqlite3.Connection:
    """Return a configured SQLite connection with dict-like row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize tables in local SQLite database or verify Supabase schema."""
    if _use_supabase:
        try:
            # Quick ping to Supabase to verify connection
            _supabase_client.table("appointments").select("id").limit(1).execute()
            logger.info("Supabase database connection verified.")
            return
        except Exception as exc:
            logger.warning("Supabase table check failed (%s). Local tables will also be prepared.", exc)

    # Initialize SQLite database
    with _get_sqlite_conn() as conn:
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                patient_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                age INTEGER NOT NULL DEFAULT 30,
                gender TEXT NOT NULL DEFAULT 'Other',
                reason TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Scheduled',
                missed_at TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_approvals (
                id TEXT PRIMARY KEY,
                appointment_id TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                action_type TEXT NOT NULL DEFAULT 'SEND_FOLLOW_UP',
                proposed_message TEXT NOT NULL,
                proposed_slots TEXT,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                appointment_id TEXT,
                agent_name TEXT NOT NULL,
                action TEXT NOT NULL,
                state_from TEXT,
                state_to TEXT,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0.0
            );
            """)

            # Seed default inventory if empty
            cur = conn.execute("SELECT COUNT(*) as count FROM inventory")
            if cur.fetchone()["count"] == 0:
                conn.executemany(
                    "INSERT INTO inventory (item_name, quantity, price) VALUES (?, ?, ?)",
                    [
                        ("Paracetamol 500mg (Box of 100)", 45, 12.50),
                        ("Amoxicillin 250mg (Bottles)", 18, 24.00),
                        ("Sterile Gauze Pads (Pack of 50)", 60, 8.75),
                        ("Blood Glucose Test Strips (100s)", 8, 35.00),
                        ("Surgical Face Masks (Box of 50)", 120, 5.50),
                        ("Digital Thermometer", 4, 15.00),
                    ]
                )
    logger.info("Local SQLite database initialized at %s", DB_PATH)


# ==============================================================================
# Appointments Data Access
# ==============================================================================

def list_appointments(status: Optional[str] = None, search: Optional[str] = None) -> list[dict]:
    """Retrieve appointments filtered by status or name/phone search."""
    if _use_supabase:
        try:
            query = _supabase_client.table("appointments").select("*").order("date", desc=False).order("time", desc=False)
            if status and status != "All":
                query = query.eq("status", status)
            res = query.execute()
            rows = res.data or []
            if search:
                s = search.lower()
                rows = [r for r in rows if s in r.get("patient_name", "").lower() or s in r.get("phone", "").lower()]
            return rows
        except Exception as exc:
            logger.error("Supabase list_appointments failed: %s. Falling back to local SQLite.", exc)

    with _get_sqlite_conn() as conn:
        sql = "SELECT * FROM appointments WHERE 1=1"
        params: list[Any] = []
        if status and status != "All":
            sql += " AND status = ?"
            params.append(status)
        if search:
            sql += " AND (LOWER(patient_name) LIKE ? OR LOWER(phone) LIKE ?)"
            s_param = f"%{search.lower()}%"
            params.extend([s_param, s_param])
        sql += " ORDER BY date ASC, time ASC"
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def get_appointment(appointment_id: str) -> Optional[dict]:
    """Retrieve a single appointment by ID."""
    if _use_supabase:
        try:
            res = _supabase_client.table("appointments").select("*").eq("id", appointment_id).limit(1).execute()
            if res.data:
                return res.data[0]
            return None
        except Exception as exc:
            logger.error("Supabase get_appointment failed: %s", exc)

    with _get_sqlite_conn() as conn:
        cursor = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def create_appointment(data: dict) -> dict:
    """Insert a new appointment record."""
    if not data.get("id"):
        # Auto-generate human-friendly ID like P-1025
        with _get_sqlite_conn() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM appointments")
            count = cursor.fetchone()["count"]
            data["id"] = f"P-{1001 + count}"

    now_iso = datetime.now(timezone.utc).isoformat()
    data.setdefault("created_at", now_iso)
    data.setdefault("updated_at", now_iso)
    data.setdefault("status", "Scheduled")

    if _use_supabase:
        try:
            res = _supabase_client.table("appointments").insert(data).execute()
            if res.data:
                return res.data[0]
        except Exception as exc:
            logger.error("Supabase create_appointment failed: %s. Saving to SQLite.", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            conn.execute("""
            INSERT OR REPLACE INTO appointments (id, patient_name, phone, age, gender, reason, date, time, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["id"], data["patient_name"], data["phone"], data.get("age", 30),
                data.get("gender", "Other"), data["reason"], data["date"], data["time"],
                data["status"], data.get("notes"), data["created_at"], data["updated_at"]
            ))
    return data


def update_appointment(appointment_id: str, updates: dict) -> Optional[dict]:
    """Update fields on an appointment record."""
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    if _use_supabase:
        try:
            res = _supabase_client.table("appointments").update(updates).eq("id", appointment_id).execute()
            if res.data:
                return res.data[0]
        except Exception as exc:
            logger.error("Supabase update_appointment failed: %s", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            set_clauses = [f"{k} = ?" for k in updates.keys()]
            values = list(updates.values())
            values.append(appointment_id)
            conn.execute(f"UPDATE appointments SET {', '.join(set_clauses)} WHERE id = ?", values)
    return get_appointment(appointment_id)


# ==============================================================================
# Pending Approvals (Human-in-the-Loop)
# ==============================================================================

def list_pending_approvals(status: str = "PENDING") -> list[dict]:
    """List pending actions awaiting staff approval."""
    if _use_supabase:
        try:
            query = _supabase_client.table("pending_approvals").select("*").order("created_at", desc=True)
            if status and status != "All":
                query = query.eq("status", status)
            res = query.execute()
            return res.data or []
        except Exception as exc:
            logger.error("Supabase list_pending_approvals failed: %s", exc)

    with _get_sqlite_conn() as conn:
        sql = "SELECT * FROM pending_approvals WHERE 1=1"
        params: list[Any] = []
        if status and status != "All":
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def get_pending_approval(approval_id: str) -> Optional[dict]:
    """Get single pending approval."""
    if _use_supabase:
        try:
            res = _supabase_client.table("pending_approvals").select("*").eq("id", approval_id).limit(1).execute()
            if res.data:
                return res.data[0]
            return None
        except Exception as exc:
            logger.error("Supabase get_pending_approval failed: %s", exc)

    with _get_sqlite_conn() as conn:
        cursor = conn.execute("SELECT * FROM pending_approvals WHERE id = ?", (approval_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def create_pending_approval(data: dict) -> dict:
    """Store an action awaiting human approval."""
    now_iso = datetime.now(timezone.utc).isoformat()
    data.setdefault("created_at", now_iso)
    data.setdefault("status", "PENDING")

    if _use_supabase:
        try:
            res = _supabase_client.table("pending_approvals").insert(data).execute()
            if res.data:
                return res.data[0]
        except Exception as exc:
            logger.error("Supabase create_pending_approval failed: %s", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            conn.execute("""
            INSERT OR REPLACE INTO pending_approvals (id, appointment_id, patient_name, phone, action_type, proposed_message, proposed_slots, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["id"], data["appointment_id"], data["patient_name"], data["phone"],
                data.get("action_type", "SEND_FOLLOW_UP"), data["proposed_message"],
                json.dumps(data.get("proposed_slots", [])), data["reason"],
                data["status"], data["created_at"]
            ))
    return data


def resolve_pending_approval(approval_id: str, status: str, reviewed_by: str = "Staff") -> Optional[dict]:
    """Mark an approval as APPROVED or REJECTED."""
    now_iso = datetime.now(timezone.utc).isoformat()
    updates = {
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": now_iso
    }

    if _use_supabase:
        try:
            res = _supabase_client.table("pending_approvals").update(updates).eq("id", approval_id).execute()
            if res.data:
                return res.data[0]
        except Exception as exc:
            logger.error("Supabase resolve_pending_approval failed: %s", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            conn.execute("""
            UPDATE pending_approvals SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?
            """, (status, reviewed_by, now_iso, approval_id))
    return get_pending_approval(approval_id)


# ==============================================================================
# Audit Logs (Tool & Agent Traces)
# ==============================================================================

def log_audit_event(
    agent_name: str,
    action: str,
    state_from: Optional[str] = None,
    state_to: Optional[str] = None,
    details: Optional[str] = None,
    appointment_id: Optional[str] = None
) -> dict:
    """Record an agent execution or state machine transition."""
    import uuid
    log_id = f"LOG-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    log_data = {
        "id": log_id,
        "appointment_id": appointment_id,
        "agent_name": agent_name,
        "action": action,
        "state_from": state_from,
        "state_to": state_to,
        "details": details or "",
        "created_at": now_iso
    }

    if _use_supabase:
        try:
            _supabase_client.table("audit_logs").insert(log_data).execute()
        except Exception as exc:
            logger.error("Supabase log_audit_event failed: %s", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            conn.execute("""
            INSERT INTO audit_logs (id, appointment_id, agent_name, action, state_from, state_to, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log_data["id"], log_data["appointment_id"], log_data["agent_name"],
                log_data["action"], log_data["state_from"], log_data["state_to"],
                log_data["details"], log_data["created_at"]
            ))
    return log_data


def list_audit_logs(limit: int = 50) -> list[dict]:
    """Retrieve recent agent actions and audit events."""
    if _use_supabase:
        try:
            res = _supabase_client.table("audit_logs").select("*").order("created_at", desc=True).limit(limit).execute()
            return res.data or []
        except Exception as exc:
            logger.error("Supabase list_audit_logs failed: %s", exc)

    with _get_sqlite_conn() as conn:
        cursor = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


# ==============================================================================
# System Stats & Dashboard Metrics
# ==============================================================================

def get_system_stats() -> dict[str, Any]:
    """Calculate live metrics for the clinic & recovery dashboard."""
    appointments = list_appointments()
    approvals = list_pending_approvals("PENDING")

    today_str = datetime.now().strftime("%Y-%m-%d")
    unique_phones = set(a.get("phone", "") for a in appointments if a.get("phone"))

    missed_count = sum(1 for a in appointments if a.get("status") in ("Missed", "No-show"))
    in_lobby_count = sum(1 for a in appointments if a.get("status") == "In lobby")
    with_doc_count = sum(1 for a in appointments if a.get("status") == "With doctor")
    scheduled_count = sum(1 for a in appointments if a.get("status") == "Scheduled")
    follow_up_count = sum(1 for a in appointments if a.get("status") == "Follow-up pending")
    rescheduled_count = sum(1 for a in appointments if a.get("status") == "Rescheduled")
    escalated_count = sum(1 for a in appointments if a.get("status") == "Escalated")
    completed_count = sum(1 for a in appointments if a.get("status") == "Completed")
    today_count = sum(1 for a in appointments if a.get("date") == today_str)

    # Recovery rate calculation
    total_lost = missed_count + follow_up_count + rescheduled_count + escalated_count
    recovery_rate_pct = round((rescheduled_count / total_lost * 100), 1) if total_lost > 0 else 0.0

    return {
        "registered_patients": len(unique_phones),
        "total_appointments": len(appointments),
        "appointments_today": today_count,
        "in_lobby": in_lobby_count,
        "with_doctor": with_doc_count,
        "scheduled": scheduled_count,
        "missed": missed_count,
        "pending_approvals": len(approvals),
        "follow_up_pending": follow_up_count,
        "recovered_rescheduled": rescheduled_count,
        "escalated_cases": escalated_count,
        "completed": completed_count,
        "recovery_rate_pct": recovery_rate_pct,
        "database_mode": "Supabase Cloud" if _use_supabase else "Local SQLite",
    }


# ==============================================================================
# Seed 30 Synthetic Clinic Appointments (Rubric Alignment)
# ==============================================================================

def seed_synthetic_data(force: bool = False) -> list[dict]:
    """
    Populate exactly 30 synthetic appointments as required by the competition rubric.
    Demonstrates before/after recovery, pending approvals, and AI safety escalation.
    """
    existing = list_appointments()
    if existing and not force:
        return existing

    today = datetime.now()
    def offset_date(days: int) -> str:
        return (today + timedelta(days=days)).strftime("%Y-%m-%d")

    raw_seed_data = [
        # (Name, Phone, Age, Gender, Reason, DayOffset, Time, Status)
        ("Ayesha Khan", "0300 1112233", 34, "Female", "Type 2 Diabetes Routine Checkup", 0, "09:30", "In lobby"),
        ("Bilal Ahmed", "0321 5557788", 52, "Male", "Essential Hypertension Management", 0, "10:00", "With doctor"),
        ("Hina Malik", "0333 9084412", 27, "Female", "Chronic Migraine Evaluation", 0, "11:15", "Scheduled"),
        ("Usman Raza", "0345 2203311", 41, "Male", "Lower Back Pain Follow-up", 0, "14:00", "Scheduled"),
        ("Zainab Ali", "0301 7764455", 8, "Female", "Seasonal Flu & Fever Consultation", 1, "09:00", "Scheduled"),
        ("Tariq Mehmood", "0312 4401199", 63, "Male", "Osteoarthritis Knee Pain Review", 1, "12:30", "Scheduled"),
        ("Fatima Noor", "0302 3344556", 29, "Female", "Prenatal 2nd Trimester Ultrasound Review", 1, "15:00", "Scheduled"),
        ("Hamza Siddiqui", "0331 6677889", 45, "Male", "Post-Surgical Wound Dressing Review", 2, "10:30", "Scheduled"),
        ("Sana Javed", "0322 8899001", 38, "Female", "Thyroid Profile & Dosage Adjustment", 2, "11:00", "Scheduled"),
        ("Khurram Shah", "0346 1122445", 55, "Male", "Post-Myocardial Infarction Checkup", 2, "16:00", "Scheduled"),

        # Missed visits (Candidate cases for CareLoop AI Recovery Monitor!)
        ("Maryam Farooq", "0304 9988776", 31, "Female", "Gestational Diabetes Follow-up", -1, "09:30", "Missed"),
        ("Waqas Aslam", "0315 2233441", 49, "Male", "Elevated Liver Enzymes Review", -1, "11:00", "Missed"),
        ("Noreen Akhtar", "0334 5566772", 60, "Female", "Osteoporosis Bone Density Scan Discussion", -2, "14:30", "Missed"),
        ("Junaid Iqbal", "0343 8899112", 23, "Male", "Bronchial Asthma Inhaler Refill", -2, "16:00", "No-show"),

        # Follow-up pending (Outreach drafted, waiting or approved)
        ("Sadia Parveen", "0305 4433221", 42, "Female", "High Cholesterol Lipid Panel Review", -3, "10:00", "Follow-up pending"),
        ("Rashid Minhas", "0323 7788990", 58, "Male", "Chronic Kidney Disease Stage 2", -3, "12:00", "Follow-up pending"),
        ("Bushra Bibi", "0335 1122334", 67, "Female", "Post-Stroke Rehabilitation Checkup", -4, "15:30", "Follow-up pending"),

        # Recovered / Rescheduled by CareLoop AI
        ("Kamran Akram", "0306 6655443", 39, "Male", "Pre-Diabetes Dietitian Consultation", 1, "11:30", "Rescheduled"),
        ("Nida Hassan", "0316 8877665", 25, "Female", "Allergic Rhinitis Symptom Review", 2, "14:00", "Rescheduled"),
        ("Imtiaz Ali", "0347 3322114", 51, "Male", "Hypertension Medication Titration", 3, "10:00", "Rescheduled"),

        # Escalated to human staff (e.g. clinical questions / safety boundaries)
        ("Rabia Sheikh", "0307 2211445", 36, "Female", "Chest Pain & Shortness of Breath", -1, "10:30", "Escalated"),
        ("Arshad Nadeem", "0324 4455667", 28, "Male", "Severe Post-Operative Fever", -2, "11:30", "Escalated"),

        # Completed historical appointments
        ("Ayesha Khan", "0300 1112233", 34, "Female", "Initial Diabetes Blood Work", -14, "10:30", "Completed"),
        ("Zahid Qureshi", "0336 9900112", 47, "Male", "Annual Wellness Physical Exam", -7, "09:00", "Completed"),
        ("Shazia Bano", "0348 5544332", 53, "Female", "Mammogram Screening Results", -6, "11:00", "Completed"),
        ("Omer Farooq", "0308 1133557", 32, "Male", "Gastroesophageal Reflux Follow-up", -5, "14:00", "Completed"),
        ("Rubina Tabassum", "0317 4466880", 61, "Female", "Cataract Pre-Operative Assessment", -4, "10:00", "Completed"),
        ("Farhan Saeed", "0325 7799113", 44, "Male", "Vitamin D Deficiency Supplement Review", -3, "15:00", "Completed"),
        ("Tahira Yasmin", "0337 2244668", 37, "Female", "Depression & Sleep Hygiene Follow-up", -2, "16:00", "Completed"),
        ("Nasir Hussain", "0349 6688002", 56, "Male", "Gout Flare-up & Uric Acid Check", -1, "12:00", "Completed"),
    ]

    # Clear existing if force
    if force and not _use_supabase:
        with _get_sqlite_conn() as conn:
            with conn:
                conn.execute("DELETE FROM appointments")
                conn.execute("DELETE FROM pending_approvals")
                conn.execute("DELETE FROM audit_logs")

    seeded: list[dict] = []
    for idx, (name, phone, age, gender, reason, offset, time_slot, status) in enumerate(raw_seed_data):
        app_id = f"P-{1001 + idx}"
        dt = offset_date(offset)
        item = {
            "id": app_id,
            "patient_name": name,
            "phone": phone,
            "age": age,
            "gender": gender,
            "reason": reason,
            "date": dt,
            "time": time_slot,
            "status": status,
            "notes": f"Synthetic pilot patient {idx+1}/30 for Agentika 2.0 evaluation",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        create_appointment(item)
        seeded.append(item)

    # Also seed a realistic pending approval and audit log for Maryam Farooq
    create_pending_approval({
        "id": "APP-M101",
        "appointment_id": "P-1011",
        "patient_name": "Maryam Farooq",
        "phone": "0304 9988776",
        "action_type": "SEND_FOLLOW_UP",
        "proposed_message": "Assalam-o-Alaikum Maryam, we noticed you missed your Gestational Diabetes Follow-up at CareLoop Clinic. Your health is our priority. Would you like to reschedule for tomorrow at 10:00 AM or Thursday at 2:30 PM? Reply with 1 or 2.",
        "proposed_slots": json.dumps(["Tomorrow 10:00 AM", "Thursday 02:30 PM"]),
        "reason": "Missed visit detected by Recovery Monitor Agent. Outreach drafted within non-clinical boundaries.",
        "status": "PENDING",
    })

    log_audit_event(
        agent_name="RecoveryMonitor",
        action="Detected missed visit for Maryam Farooq. Dispatched follow-up generation.",
        state_from="Scheduled",
        state_to="Missed",
        details="Duplicate check passed. No prior active outreach found.",
        appointment_id="P-1011"
    )

    log_audit_event(
        agent_name="OutreachAgent",
        action="Generated personalized empathetic SMS with 2 slots. Parked for staff approval.",
        state_from="Missed",
        state_to="Follow-up pending",
        details="Non-clinical boundary validated: Zero diagnostic or medication claims present.",
        appointment_id="P-1011"
    )

    logger.info("Successfully seeded 30 synthetic appointments.")
    return seeded


# ==============================================================================
# Clinic Inventory & ERP Compatibility
# ==============================================================================

def get_inventory_status() -> list[dict]:
    """Return all inventory rows as dicts ordered by id."""
    if _use_supabase:
        try:
            res = _supabase_client.table("inventory").select("*").order("id", desc=False).execute()
            return res.data or []
        except Exception as exc:
            logger.error("Supabase get_inventory_status failed: %s", exc)

    with _get_sqlite_conn() as conn:
        cursor = conn.execute("SELECT id, item_name, quantity, price FROM inventory ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]


def update_stock(item_name: str, quantity_change: int) -> bool:
    """Atomically adjust stock, reject if missing or would go negative."""
    if _use_supabase:
        try:
            curr = _supabase_client.table("inventory").select("*").eq("item_name", item_name).limit(1).execute()
            if not curr.data:
                return False
            row = curr.data[0]
            new_qty = row["quantity"] + quantity_change
            if new_qty < 0:
                return False
            res = _supabase_client.table("inventory").update({"quantity": new_qty}).eq("id", row["id"]).execute()
            return len(res.data) > 0
        except Exception as exc:
            logger.error("Supabase update_stock failed: %s", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE inventory SET quantity = quantity + ? WHERE item_name = ? AND quantity + ? >= 0",
                (quantity_change, item_name, quantity_change),
            )
            return cursor.rowcount > 0


def add_new_item(item_name: str, quantity: int, price: float) -> int:
    """Insert a new product after validating inputs, return new row id."""
    cleaned_name = item_name.strip()
    if not cleaned_name:
        raise ValueError("item_name must be a non-empty string")
    if quantity < 0 or price < 0:
        raise ValueError("quantity and price must be >= 0")

    if _use_supabase:
        try:
            res = _supabase_client.table("inventory").insert({
                "item_name": cleaned_name,
                "quantity": quantity,
                "price": float(price)
            }).execute()
            if res.data:
                return res.data[0]["id"]
        except Exception as exc:
            logger.error("Supabase add_new_item failed: %s", exc)

    with _get_sqlite_conn() as conn:
        with conn:
            cursor = conn.execute(
                "INSERT INTO inventory (item_name, quantity, price) VALUES (?, ?, ?)",
                (cleaned_name, quantity, float(price)),
            )
            return int(cursor.lastrowid)


if __name__ == "__main__":
    init_db()
    seed_synthetic_data()
    print("Database test completed:", get_db_status())
    print("System stats:", get_system_stats())