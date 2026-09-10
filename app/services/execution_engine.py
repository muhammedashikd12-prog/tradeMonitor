"""Execution engine. Only active in EXECUTION_MODE=on. Validates everything
before sending an order, places hedges BEFORE shorts, confirms every fill,
and never assumes an order was filled."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from app.brokers.base import BrokerClient
from app.models import IronCondorStructure, PositionSize, OrderResult
from app.config import settings


@dataclass
class ExecutionResult:
    success: bool
    orders: dict[str, OrderResult] = field(default_factory=dict)
    aborted_reason: str = ""


class ExecutionEngine:
    def __init__(self, broker: BrokerClient, symbol_prefix: str, lot_size: int = None):
        self.broker = broker
        self.symbol_prefix = symbol_prefix
        self.lot_size = lot_size or settings.nifty_lot_size

    def _resolve_symbol(self, leg) -> str:
        """ALWAYS prefer the broker's own symbol string (captured at chain
        fetch time in leg.symbol). Only reconstruct as a last resort — if
        this path is hit, the option chain wasn't fetched from the real
        broker (e.g. a hand-built test structure), and the reconstructed
        symbol has NOT been verified against Fyers' actual convention."""
        if leg.symbol:
            return leg.symbol
        import warnings
        warnings.warn(
            f"No broker symbol on leg {leg.role} (strike={leg.strike}) — falling back to an "
            "UNVERIFIED reconstructed symbol. This will likely fail against the real Fyers API. "
            "Fetch the option chain via FyersClient so legs carry a real broker symbol.",
            RuntimeWarning,
        )
        return f"{self.symbol_prefix}{int(leg.strike)}{leg.option_type}"

    def pre_trade_validation(self, structure: IronCondorStructure, size: PositionSize,
                              available_margin: float) -> tuple[bool, str]:
        if settings.execution_mode != "on":
            return False, "Execution mode is OFF — analysis/alert only"
        if size.lots <= 0:
            return False, "Position size resolved to 0 lots — risk limits do not permit a trade"
        if size.margin_required > available_margin:
            return False, "Insufficient margin for sized position"
        if structure.risk_reward is not None and structure.risk_reward < 0.15:
            return False, "Risk/reward too poor to justify execution"
        # Confirm a hedge exists for every short (structural invariant, always true here,
        # but checked explicitly per the "confirm hedge exists before short order" rule)
        roles = {leg.role for leg in structure.legs}
        if "call_hedge" not in roles or "put_hedge" not in roles:
            return False, "Hedge leg missing — refusing to place naked short"
        return True, "Validated"

    def _place_and_confirm(self, symbol: str, side: str, qty: int, tag: str,
                            timeout_s: float = 10.0, poll_interval: float = 1.0) -> OrderResult:
        result = self.broker.place_order(symbol=symbol, side=side, quantity=qty, tag=tag)
        if result.status == "REJECTED" or not result.order_id:
            return result
        waited = 0.0
        while waited < timeout_s:
            time.sleep(poll_interval)
            waited += poll_interval
            status = self.broker.get_order_status(result.order_id)
            if status.status in ("FILLED", "REJECTED"):
                return status
        return OrderResult(order_id=result.order_id, status="UNKNOWN", raw={"note": "fill not confirmed within timeout"})

    def execute_iron_condor(self, structure: IronCondorStructure, size: PositionSize,
                             expiry: str, available_margin: float) -> ExecutionResult:
        ok, msg = self.pre_trade_validation(structure, size, available_margin)
        if not ok:
            return ExecutionResult(success=False, aborted_reason=msg)

        orders: dict[str, OrderResult] = {}
        qty = size.quantity

        # Hedges FIRST (buy protection before selling anything)
        for role in ("call_hedge", "put_hedge"):
            leg = next(l for l in structure.legs if l.role == role)
            symbol = self._resolve_symbol(leg)
            result = self._place_and_confirm(symbol, "BUY", qty, tag=f"condor_{role}")
            orders[role] = result
            if result.status != "FILLED":
                return ExecutionResult(success=False, orders=orders,
                                        aborted_reason=f"Hedge leg {role} did not fill — aborting before shorting")

        # Shorts SECOND, only after both hedges confirmed filled
        for role in ("call_short", "put_short"):
            leg = next(l for l in structure.legs if l.role == role)
            symbol = self._resolve_symbol(leg)
            result = self._place_and_confirm(symbol, "SELL", qty, tag=f"condor_{role}")
            orders[role] = result
            if result.status != "FILLED":
                # A short failed to fill but its hedge is already on — flag loudly,
                # do NOT auto-retry or auto-adjust size.
                return ExecutionResult(success=False, orders=orders,
                                        aborted_reason=f"Short leg {role} did not fill — hedge is on, manual review required")

        return ExecutionResult(success=True, orders=orders)

    def unwind_vertical(self, structure: IronCondorStructure, roles: list[str], expiry: str,
                         qty: int) -> ExecutionResult:
        """Closes a stop-lossed vertical: short leg closed first (buy to cover),
        then the hedge (sell to close) — never leaves the hedge on without reason."""
        orders: dict[str, OrderResult] = {}
        for role in roles:
            leg = next(l for l in structure.legs if l.role == role)
            symbol = self._resolve_symbol(leg)
            side = "BUY" if leg.action == "SELL" else "SELL"  # reverse the original action
            result = self._place_and_confirm(symbol, side, qty, tag=f"unwind_{role}")
            orders[role] = result
            if result.status != "FILLED":
                return ExecutionResult(success=False, orders=orders,
                                        aborted_reason=f"Failed to confirm unwind of {role} — manual intervention required")
        return ExecutionResult(success=True, orders=orders)
