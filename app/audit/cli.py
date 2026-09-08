"""Verify the integrity of the append-only audit hash chain.

Usage: ``rag-audit verify [--org-id ORG_ID]``. With no ``--org-id`` every organization that
has audit history is checked. Exit code is non-zero if any chain is broken.
"""

import argparse
import asyncio

from app.api.dependencies import get_metadata_engine
from app.audit import AuditRepository
from app.core.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="check hash-chain integrity")
    verify.add_argument("--org-id")
    arguments = parser.parse_args()
    return asyncio.run(_verify(arguments))


async def _verify(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.metadata_store_backend != "postgresql":
        print("Audit verification requires METADATA_STORE_BACKEND=postgresql")
        return 2
    repository = AuditRepository(get_metadata_engine())
    org_ids = [arguments.org_id] if arguments.org_id else await repository.organization_ids()
    if not org_ids:
        print("No audit history found.")
        return 0
    broken = 0
    for org_id in org_ids:
        result = await repository.verify_chain(org_id)
        pruned = (
            f" (history pruned through sequence {result.pruned_through})"
            if result.pruned_through
            else ""
        )
        if result.ok:
            print(f"{org_id}: OK{pruned}")
        else:
            broken += 1
            print(f"{org_id}: BROKEN at sequence {result.first_broken_sequence}{pruned}")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
