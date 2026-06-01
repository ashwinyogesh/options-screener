"""DD Coach service package.

V1 scope (see docs/DD_COACH_METHODOLOGY.md — TBD Phase 1):
  - 8-screen DD wizard backed by an immutable, dated journal entry
  - Cosmos DB-backed persistence (container: dd_entries)
  - Single-user (user_id="default") for V1

Layering: this package owns business logic. The HTTP layer in
backend/routers/dd_coach.py only validates / delegates / converts.
"""
