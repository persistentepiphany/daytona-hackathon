"""Per-run spend ceilings. TTL is the backstop; this is the front stop.

Charges are appended to the ledger and summed on read, so budget state survives
restarts and is auditable. Exceeding a ceiling raises before the spend happens.
"""

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
        return self.ledger.sum_charges(self.run_id, kind)

    def charge(self, kind: str, amount: float, note: str | None = None) -> None:
        # read-then-write under the ledger's lock: charging from two threads used
        # to interleave with another statement mid-transaction and blow up with
        # 'cannot commit - no transaction is active'
        with self.ledger.lock:
            spent = self.spent(kind)
            ceiling = self.ceilings.get(kind)
            if ceiling is not None and spent + amount > ceiling:
                raise BudgetExceeded(
                    f"{kind}: {spent} + {amount} exceeds ceiling {ceiling} for run {self.run_id}"
                )
            self.ledger.add_charge(self.run_id, kind, amount, note)

    def remaining(self, kind: str) -> float | None:
        ceiling = self.ceilings.get(kind)
        return None if ceiling is None else ceiling - self.spent(kind)
