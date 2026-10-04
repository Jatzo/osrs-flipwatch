"""Choose which flips to put in your Grand Exchange offer slots with the cash you have."""

from collections.abc import Iterable
from dataclasses import dataclass

from flipwatch.config import BUY_LIMIT_WINDOW_SECONDS, GE_OFFER_SLOTS
from flipwatch.models import Opportunity
from flipwatch.scanner import ScanSettings, rank

# Buy limits reset every four hours, so a full round of flips can repeat that often.
ROUND_HOURS = BUY_LIMIT_WINDOW_SECONDS / 3600


@dataclass(frozen=True)
class PlanSettings:
    capital: int = 50_000_000
    slots: int = GE_OFFER_SLOTS
    # Big margins on thin trading are usually traps, so they are left out of a plan.
    min_confidence: int = 40

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.slots <= 0:
            raise ValueError("slots must be positive")
        if not 0 <= self.min_confidence <= 100:
            raise ValueError("min_confidence must be 0 to 100")


@dataclass(frozen=True)
class PlannedFlip:
    opportunity: Opportunity
    quantity: int

    @property
    def cost(self) -> int:
        return self.opportunity.buy_price * self.quantity

    @property
    def profit(self) -> int:
        return self.opportunity.margin * self.quantity


@dataclass(frozen=True)
class Plan:
    flips: list[PlannedFlip]
    settings: PlanSettings

    @property
    def cash_used(self) -> int:
        return sum(flip.cost for flip in self.flips)

    @property
    def cash_left(self) -> int:
        return self.settings.capital - self.cash_used

    @property
    def profit(self) -> int:
        """Profit from one round, if every offer fills."""
        return sum(flip.profit for flip in self.flips)

    @property
    def profit_per_hour(self) -> float:
        """Assumes a full round fills and repeats each time buy limits reset."""
        return self.profit / ROUND_HOURS


def plan_scan_settings(free_to_play: bool = False) -> ScanSettings:
    """The usual scan filters without the profit floor, since a small flip can still be the
    best use of a spare slot."""
    return ScanSettings(min_profit=0, members=False if free_to_play else None)


def make_plan(opportunities: Iterable[Opportunity], settings: PlanSettings) -> Plan:
    """Fill each slot in turn with the flip that adds the most profit with the cash left.

    When the cash left cannot cover a flip's full quantity, it buys as many as it can, so
    expensive items take a smaller share rather than being skipped. Each item takes at
    most one slot, because its buy limit already caps how many can be bought.
    """
    candidates = [o for o in rank(opportunities) if o.confidence.score >= settings.min_confidence]
    cash = settings.capital
    flips: list[PlannedFlip] = []
    while candidates and len(flips) < settings.slots:
        best, best_quantity = None, 0
        for opportunity in candidates:
            quantity = min(opportunity.quantity, cash // opportunity.buy_price)
            if best is None or quantity * opportunity.margin > best_quantity * best.margin:
                best, best_quantity = opportunity, quantity
        if best is None or best_quantity == 0:
            break
        flips.append(PlannedFlip(best, best_quantity))
        cash -= best.buy_price * best_quantity
        candidates.remove(best)
    return Plan(flips, settings)
