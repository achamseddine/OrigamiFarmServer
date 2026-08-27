"""Background worker entrypoint.

v0.1 foundation ships the control-plane schema and API surface for
backup_job / tenant_export (see app/backups) but not yet the scheduled
jobs that actually produce a database backup or a tenant export package —
that lands with Milestone/Phase E (Data Operations). This process starts
cleanly and logs its intent so `docker-compose up` works end to end, but
it does not yet claim PENDING jobs. Wiring a real queue (Celery/Dramatiq/
ARQ per ARCHITECTURE.md) and the backup/export producers is tracked
follow-up work, not a hidden gap.
"""

from __future__ import annotations

import time

from app.common.logging import configure_logging, get_logger
from app.config import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("workers")
    log.info("workers.starting", environment=settings.environment)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
