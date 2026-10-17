-- ============================================================================
-- CareLoop AI: Supabase Database Schema
-- ============================================================================
-- Description: Complete schema for CareLoop AI Healthcare Recovery System
-- Compatible with Supabase PostgreSQL and SQLite local fallback.
-- Paste this script into the Supabase SQL Editor and click "Run".
-- ============================================================================

-- 1. APPOINTMENTS TABLE
CREATE TABLE IF NOT EXISTS appointments (
    id VARCHAR(50) PRIMARY KEY,
    patient_name VARCHAR(255) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    age INTEGER NOT NULL DEFAULT 30,
    gender VARCHAR(20) NOT NULL DEFAULT 'Other',
    reason TEXT NOT NULL,
    date DATE NOT NULL,
    time VARCHAR(10) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'Scheduled',
    missed_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for quick status and date lookups
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date);
CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments(phone);

-- 2. PENDING APPROVALS TABLE (Human-in-the-Loop Approval Gate)
CREATE TABLE IF NOT EXISTS pending_approvals (
    id VARCHAR(50) PRIMARY KEY,
    appointment_id VARCHAR(50) NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    patient_name VARCHAR(255) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    action_type VARCHAR(50) NOT NULL DEFAULT 'SEND_FOLLOW_UP',
    proposed_message TEXT NOT NULL,
    proposed_slots TEXT,
    reason TEXT NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals(status);

-- 3. AUDIT LOGS TABLE (Agent Action & State Transition Trace)
CREATE TABLE IF NOT EXISTS audit_logs (
    id VARCHAR(50) PRIMARY KEY,
    appointment_id VARCHAR(50),
    agent_name VARCHAR(100) NOT NULL,
    action TEXT NOT NULL,
    state_from VARCHAR(50),
    state_to VARCHAR(50),
    details TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);

-- 4. CLINIC INVENTORY & ERP TABLE
CREATE TABLE IF NOT EXISTS inventory (
    id BIGSERIAL PRIMARY KEY,
    item_name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    price NUMERIC(10, 2) NOT NULL DEFAULT 0.00
);

-- ============================================================================
-- Row Level Security (RLS) Policies (Supabase Best Practice)
-- Allows public anonymous access for this demo/prototype
-- ============================================================================
ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;

-- Allow anon full access for demo application
CREATE POLICY IF NOT EXISTS "Allow anon full access to appointments" ON appointments
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY IF NOT EXISTS "Allow anon full access to pending_approvals" ON pending_approvals
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY IF NOT EXISTS "Allow anon full access to audit_logs" ON audit_logs
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY IF NOT EXISTS "Allow anon full access to inventory" ON inventory
    FOR ALL USING (true) WITH CHECK (true);

-- ============================================================================
-- Initial Sample Inventory for Clinic Supplies
-- ============================================================================
INSERT INTO inventory (item_name, quantity, price)
VALUES 
    ('Paracetamol 500mg (Box of 100)', 45, 12.50),
    ('Amoxicillin 250mg (Bottles)', 18, 24.00),
    ('Sterile Gauze Pads (Pack of 50)', 60, 8.75),
    ('Blood Glucose Test Strips (100s)', 8, 35.00),
    ('Surgical Face Masks (Box of 50)', 120, 5.50),
    ('Digital Thermometer', 4, 15.00)
ON CONFLICT DO NOTHING;
