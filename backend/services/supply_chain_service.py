"""Supply Chain extraction service — re-export shim (DEPRECATED).

The supply-chain pipeline lives in :mod:`services.supply_chain`. This
module survives only so existing import paths
(``from services.supply_chain_service import get_supply_chain``) keep
working through the Phase 1 / Phase 2 transition. Scheduled for
removal once ``backend/routers/supply_chain.py`` migrates to the new
package directly.
"""
from __future__ import annotations

from services.supply_chain.pipeline import get_supply_chain

__all__ = ["get_supply_chain"]
