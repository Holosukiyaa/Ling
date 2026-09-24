"""SQLite schema for slots, tickets, and consumption locks.

Operation ids are not stored. The unit of work port does not receive them,
and this package does not add a write API that no caller uses. A repeated
insert of the same slot, ticket, or mentor lock conflicts on the primary key.
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    slot_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    issuer_slot_id TEXT NOT NULL REFERENCES slots(slot_id),
    content TEXT NOT NULL,
    state TEXT NOT NULL,
    claimant_slot_id TEXT REFERENCES slots(slot_id),
    review_result TEXT,
    CHECK (state IN ('queued', 'claimed', 'submitted', 'accepted', 'rejected', 'consumed')),
    CHECK (review_result IS NULL OR review_result IN ('accepted', 'rejected')),
    CHECK (
        (state = 'queued' AND claimant_slot_id IS NULL AND review_result IS NULL)
        OR (state = 'claimed' AND claimant_slot_id IS NOT NULL AND review_result IS NULL)
        OR (state = 'submitted' AND claimant_slot_id IS NOT NULL AND review_result IS NULL)
        OR (state = 'accepted' AND claimant_slot_id IS NOT NULL AND review_result = 'accepted')
        OR (state = 'rejected' AND claimant_slot_id IS NOT NULL AND review_result = 'rejected')
        OR (state = 'consumed' AND claimant_slot_id IS NOT NULL
            AND review_result IN ('accepted', 'rejected'))
    )
);

CREATE TABLE IF NOT EXISTS consumption_locks (
    mentor_slot_id TEXT PRIMARY KEY REFERENCES slots(slot_id),
    ticket_id TEXT UNIQUE REFERENCES tickets(ticket_id)
);
"""


def initialize(connection: sqlite3.Connection) -> None:
    """Create the Ling tables when they are missing. Safe to run again."""

    connection.executescript(SCHEMA)
    connection.execute("PRAGMA foreign_keys = ON")
