"""Position sizing from total capital and configured max-risk-per-trade %.
Never recommends a size that violates the user's max risk setting."""
from __future__ import annotations
from app.models import IronCondorStructure, PositionSize
from app.config import settings


def size_position(structure: IronCondorStructure, margin_per_lot: float,
                   available_margin: float, lot_size: int = 75) -> PositionSize:
    max_risk_rupees = settings.total_capital * settings.max_risk_per_trade_pct / 100
    max_loss_per_lot = structure.max_loss  # already computed for 1 lot in wing_width_engine

    if max_loss_per_lot <= 0:
        return PositionSize(lots=0, quantity=0, margin_required=0, max_loss_rupees=0, risk_pct_of_capital=0)

    max_lots_by_risk = int(max_risk_rupees // max_loss_per_lot)
    max_lots_by_margin = int(available_margin // margin_per_lot) if margin_per_lot > 0 else 0
    lots = max(0, min(max_lots_by_risk, max_lots_by_margin))

    total_max_loss = lots * max_loss_per_lot
    total_margin = lots * margin_per_lot
    risk_pct = round(total_max_loss / settings.total_capital * 100, 2) if settings.total_capital else 0

    return PositionSize(
        lots=lots, quantity=lots * lot_size, margin_required=round(total_margin, 2),
        max_loss_rupees=round(total_max_loss, 2), risk_pct_of_capital=risk_pct,
    )
