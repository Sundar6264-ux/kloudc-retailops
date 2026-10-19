# syntax=docker/dockerfile:1
# The nightly backup job of the shared service (docker-compose.platform.yml,
# profile "backup"): the PostgreSQL 16 client programs (same major version as
# the database) + Python for backend/scripts/backup/backup_all.py.
FROM postgres:16-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 tini \
    && rm -rf /var/lib/apt/lists/*
COPY backend/scripts/backup/pg_backup.py backend/scripts/backup/backup_all.py /opt/retailops/backup/
COPY deploy/cloud-vm/backup-loop.sh /opt/retailops/backup/backup-loop.sh
RUN chmod 0755 /opt/retailops/backup/backup-loop.sh \
    && mkdir -p /backups && chown postgres:postgres /backups
VOLUME ["/backups"]
USER postgres
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/retailops/backup/backup-loop.sh"]
