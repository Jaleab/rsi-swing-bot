from dataclasses import dataclass, field
from typing import Optional

@dataclass
class GuardResult:
    """
    Represents the outcome of a guardrail check.
    
    Attributes:
        allowed (bool): True if the action is allowed, False if blocked.
        reason (str): A human-readable reason for the decision (especially if blocked).
        guard_name (str): The name of the guard that made the decision (e.g., "MAX_TOTAL_POSITIONS", "COOLDOWN").
        details (Optional[str]): Optional additional details about the guard check.
    """
    allowed: bool
    reason: str
    guard_name: str
    details: Optional[str] = None