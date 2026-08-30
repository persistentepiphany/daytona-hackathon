"""Per-run spend ceilings. TTL is the backstop; this is the front stop.

Charges are appended to the ledger and summed on read, so budget state survives
restarts and is auditable. Exceeding a ceiling raises before the spend happens.
"""

import time

from .ledger import Ledger


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, ledger: Ledger, run_id: str, ceilings: dict[str, float]):
        """ceilings: e.g. {"sandbox_minutes": 600, "llm_calls": 200, "parallel_calls": 12}"""
        self.ledger = ledger
        self.run_id = run_id
        self.ceilings = dict(ceilings)

    def spent(self, kind: str) -> float:
        # the lock guards this connection, shared across worker threads; it does not
        # make charge() atomic - the ceiling check above is a separate acquisition
        with self.ledger.lock:
            row = self.ledger.db.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM budget_charges WHERE run_id=? AND kind=?",
                (self.run_id, kind),
            ).fetchone()
        return float(row["total"])

    def charge(self, kind: str, amount: float, note: str | None = None) -> None:
        ceiling = self.ceilings.get(kind)
        if ceiling is not None and self.spent(kind) + amount > ceiling:
            self.ledger.bus.emit(self.run_id, "budget.tick", {
                "kind": kind, "spent": self.spent(kind), "ceiling": ceiling,
                "state": "exceeded",
            })
            raise BudgetExceeded(
                f"{kind}: {self.spent(kind)} + {amount} exceeds ceiling {ceiling} for run {self.run_id}"
            )
        with self.ledger.lock:
            self.ledger.db.execute(
                "INSERT INTO budget_charges (run_id, kind, amount, note, created_at) VALUES (?,?,?,?,?)",
                (self.run_id, kind, amount, note, time.time()),
            )
            self.ledger.db.commit()
        self.ledger.bus.emit(self.run_id, "budget.tick", {
            "kind": kind, "spent": self.spent(kind), "ceiling": ceiling,
        })

    def remaining(self, kind: str) -> float | None:
        ceiling = self.ceilings.get(kind)
        return None if ceiling is None else ceiling - self.spent(kind)
