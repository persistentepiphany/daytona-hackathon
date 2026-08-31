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

    def check(self, kind: str, amount: float) -> None:
        """Would this spend exceed the ceiling? Raises without recording anything.

        Callers that can fail after the check (a sandbox create the provider may
        refuse) check first and charge only once the spend is real.
        """
        ceiling = self.ceilings.get(kind)
        if ceiling is None:
            return
        spent = self.spent(kind)
        if spent + amount > ceiling:
            raise BudgetExceeded(
                f"{kind}: {spent} + {amount} exceeds ceiling {ceiling} for run {self.run_id}"
            )

    def charge(self, kind: str, amount: float, note: str | None = None) -> None:
        # read-then-write under one acquisition of the ledger's lock, so the ceiling
        # check and the charge cannot interleave with another thread's charge; doing
        # this outside the lock is what raced P2 into 'cannot commit - no transaction
        # is active' and forced experiments to run one at a time
        ceiling = self.ceilings.get(kind)
        with self.ledger.lock:
            spent = self.spent(kind)
            if ceiling is not None and spent + amount > ceiling:
                exceeded = True
            else:
                exceeded = False
                self.ledger.add_charge(self.run_id, kind, amount, note)
                spent += amount
        # the bus writes to the same connection, so it is emitted outside the lock
        self.ledger.bus.emit(self.run_id, "budget.tick", {
            "kind": kind, "spent": spent, "ceiling": ceiling,
            **({"state": "exceeded"} if exceeded else {}),
        })
        if exceeded:
            raise BudgetExceeded(
                f"{kind}: {spent} + {amount} exceeds ceiling {ceiling} for run {self.run_id}"
            )

    def remaining(self, kind: str) -> float | None:
        ceiling = self.ceilings.get(kind)
        return None if ceiling is None else ceiling - self.spent(kind)
