"""
Alias module for recovery_monitor / recovery_moniter compatibility.
"""
from agents.recovery_moniter import (
    RecoveryMonitorAgent,
    Anomaly,
    AnomalyType,
)

__all__ = ["RecoveryMonitorAgent", "Anomaly", "AnomalyType"]
