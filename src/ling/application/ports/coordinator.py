"""No outbound agent-coordinator protocol.

Ling completes register, heartbeat, dispatch, claim, submit, review, consume,
and file locks locally. This module defines no port and no HTTP contract.
A future adapter may be introduced only for a real external system.
"""
