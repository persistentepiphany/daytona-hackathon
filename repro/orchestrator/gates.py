"""Gate state machine: user approvals are recorded once and enforced deterministically.

G1 approve-and-freeze must pass before any sandbox is created (no sandbox spend
before Gate 1). G2 gates GPU runs; G3 gates the final push. Gates are persisted in
the ledger, append-only, and can never be un-approved: follow-up work happens under
a new prereg document, never by mutating an approved one.
"""

from .ledger import Ledger

GATES = ("G1", "G2", "G3", "P4")  # P4 approves the single adaptive round's prereg-002


class GateError(RuntimeError):
    pass


class Gates:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def approve(self, run_id: str, gate: str, approver: str) -> None:
        if gate not in GATES:
            raise GateError(f"unknown gate {gate}")
        if gate != "G1" and not self.passed(run_id, "G1"):
            raise GateError(f"{gate} cannot be approved before G1")
        try:
            self.ledger.add_gate(run_id, gate, approver)
        except Exception as e:
            raise GateError(f"gate {gate} already approved for {run_id}") from e
        self.ledger.log_event(run_id, "gate_approved", {"gate": gate, "approver": approver})

    def passed(self, run_id: str, gate: str) -> bool:
        return self.ledger.has_gate(run_id, gate)

    def require(self, run_id: str, gate: str) -> None:
        if not self.passed(run_id, gate):
            raise GateError(f"gate {gate} not approved for run {run_id}")
