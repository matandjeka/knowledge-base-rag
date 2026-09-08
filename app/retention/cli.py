"""Apply per-workspace retention policies, cascading source deletion through every store.

Usage: ``rag-retention apply [--dry-run]``. ``--dry-run`` reports what would be removed
without deleting anything. Audit history is always kept at least the floor number of days.
"""

import argparse
import asyncio

from app.api.dependencies import get_metadata_engine, get_source_repository, get_source_storage
from app.audit import AuditRepository
from app.auth.organizations import OrganizationRepository
from app.core.config import get_settings
from app.retention import RetentionRepository, apply_retention


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    apply_parser = subparsers.add_parser("apply", help="enforce configured retention policies")
    apply_parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    return asyncio.run(_apply(arguments))


async def _apply(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.metadata_store_backend != "postgresql":
        print("Retention enforcement requires METADATA_STORE_BACKEND=postgresql")
        return 2
    engine = get_metadata_engine()
    organizations = OrganizationRepository(engine)

    async def organization_lookup(workspace_id: str) -> str | None:
        org = await organizations.organization_for_workspace(workspace_id)
        return org.id if org else None

    reports = await apply_retention(
        retention=RetentionRepository(engine),
        audit=AuditRepository(engine),
        source_repository=get_source_repository(),
        source_storage=get_source_storage(),
        organization_lookup=organization_lookup,
        dry_run=arguments.dry_run,
    )
    label = "would remove" if arguments.dry_run else "removed"
    for report in reports:
        print(
            f"{label}: audit={report.audit_events_purged} "
            f"query_counters={report.query_counters_purged} "
            f"sources={len(report.sources_purged)}"
        )
    if not reports:
        print("No retention policies are configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
