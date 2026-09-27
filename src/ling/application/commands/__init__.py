"""Write-side use cases. Each module exposes `execute`."""

from ling.application.commands.abandon_claim import execute as abandon_claim
from ling.application.commands.acquire_controller_lease import execute as acquire_controller_lease
from ling.application.commands.acquire_file_lock import execute as acquire_file_lock
from ling.application.commands.attach import execute as attach
from ling.application.commands.claim import execute as claim
from ling.application.commands.consume import execute as consume
from ling.application.commands.detach import execute as detach
from ling.application.commands.dispatch import execute as dispatch
from ling.application.commands.heartbeat import execute as heartbeat
from ling.application.commands.provision_slot import execute as provision_slot
from ling.application.commands.register_slot import execute as register_slot
from ling.application.commands.release_controller_lease import execute as release_controller_lease
from ling.application.commands.renew_controller_lease import execute as renew_controller_lease
from ling.application.commands.review import execute as review
from ling.application.commands.submit import execute as submit

__all__ = [
    "abandon_claim",
    "acquire_controller_lease",
    "acquire_file_lock",
    "attach",
    "claim",
    "consume",
    "detach",
    "dispatch",
    "heartbeat",
    "provision_slot",
    "register_slot",
    "release_controller_lease",
    "renew_controller_lease",
    "review",
    "submit",
]
