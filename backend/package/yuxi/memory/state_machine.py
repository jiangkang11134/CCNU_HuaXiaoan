from typing import Final

SESSION_TRANSITIONS: Final = {
    "observed": {"active", "retracted", "expired"},
    "active": {"superseded", "retracted", "conflicted", "expired"},
}
MEMORY_TRANSITIONS: Final = {
    "candidate": {"pending_confirmation", "confirmed", "rejected", "superseded", "conflicted", "expired"},
    "pending_confirmation": {"confirmed", "rejected", "superseded", "conflicted", "expired"},
    "confirmed": {"superseded", "retracted", "tombstoned", "conflicted", "expired"},
}

def transition(current: str, target: str, *, memory: bool = True) -> str:
    allowed = MEMORY_TRANSITIONS if memory else SESSION_TRANSITIONS
    if target not in allowed.get(current, set()):
        kind = "memory" if memory else "session fact"
        raise ValueError(f"invalid {kind} transition: {current} -> {target}")
    return target
