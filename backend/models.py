"""
CareLoop AI: Pydantic Validation Models
"""

from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppointmentCreate(BaseModel):
    """Validation schema for a new patient appointment."""
    model_config = ConfigDict(str_strip_whitespace=True)

    patient_name: str = Field(min_length=1, description="Patient full name")
    phone: str = Field(min_length=8, description="Contact phone number")
    age: int = Field(ge=0, le=120, default=30, description="Patient age")
    gender: Literal["Female", "Male", "Other"] = Field(default="Other")
    reason: str = Field(min_length=1, description="Reason for visit or clinical diagnosis")
    date: str = Field(description="Appointment date YYYY-MM-DD")
    time: str = Field(description="Appointment time HH:MM")
    notes: Optional[str] = Field(default=None)


class AppointmentUpdate(BaseModel):
    """Validation schema for updating appointment status or details."""
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    notes: Optional[str] = None


class ApprovalResolveRequest(BaseModel):
    """Payload when human staff approves or rejects an outreach message."""
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["APPROVED", "REJECTED"]
    edited_message: Optional[str] = Field(default=None, description="Optional staff edit of the message before sending")
    reviewed_by: Optional[str] = Field(default="Staff", description="Name of the staff member reviewing the action")


class SimulateReplyRequest(BaseModel):
    """Payload to simulate a patient reply during competition live demos."""
    model_config = ConfigDict(str_strip_whitespace=True)

    appointment_id: str
    reply_text: str = Field(description="Simulated patient reply via SMS/WhatsApp")
    scenario: Literal["slot_selection", "clinical_question", "slot_conflict", "custom"] = "slot_selection"


class InventoryItem(BaseModel):
    """Validation schema for an inventory record."""
    model_config = ConfigDict(str_strip_whitespace=True)

    id: Optional[int] = Field(default=None, description="Database-assigned primary key")
    item_name: str = Field(min_length=1, description="Item name, non-empty")
    quantity: int = Field(ge=0, description="Stock quantity, must be >= 0")
    price: float = Field(ge=0, description="Unit price, must be >= 0")


class OrderRequest(BaseModel):
    """Validation schema for adding or removing stock."""
    model_config = ConfigDict(str_strip_whitespace=True)

    item_name: str = Field(min_length=1, description="Item name, non-empty")
    quantity_to_add_or_remove: int = Field(description="Positive to add stock, negative to remove, must not be 0")

    @field_validator("quantity_to_add_or_remove")
    @classmethod
    def quantity_must_be_nonzero(cls, v: int) -> int:
        """Reject zero quantity changes."""
        if v == 0:
            raise ValueError("quantity_to_add_or_remove must not be 0")
        return v