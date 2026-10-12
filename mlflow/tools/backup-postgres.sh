#!/usr/bin/env bash
#
# Backup del registry MLflow (PostgreSQL) con pg_dump in formato custom.
#
# I dump restano SOLO in WSL in data/postgres-backups/: quella directory è
# dentro data/ (esclusa dal re-sync repo->WSL come tutto il resto di data/) e
# non è montata in nessun container, quindi né un re-sync né un avvio
# difettoso di Docker possono cancellare o inquinare i backup.
#
# Garanzie contro l'avvio sporco:
#   - file sempre timestampati, mai un singolo file sovrascritto;
#   - se il registry è vuoto (o PostgreSQL non risponde) NON fa il dump,
#     NON ruota nulla ed esce con errore: un DB azzerato non può né
#     inquinare né far scadere la storia;
#   - la rotazione (tiene gli ultimi RETENTION_COUNT) avviene solo dopo
#     un dump verificato (dimensione minima + pg_restore --list OK).
#
# Uso manuale:
#   /opt/mlops-local-poc/mlflow/tools/backup-postgres.sh
# Uso da cron (ogni 30 minuti, log in data/postgres-backups/cron.log):
#   */30 * * * * /opt/mlops-local-poc/mlflow/tools/backup-postgres.sh >> /opt/mlops-local-poc/mlflow/data/postgres-backups/cron.log 2>&1
#
# Restore (manuale, a stack fermo per mlflow):
#   cd /opt/mlops-local-poc/mlflow
#   docker compose --env-file .env stop mlflow
#   docker cp data/postgres-backups/<file>.dump mlops-postgres:/tmp/restore.dump
#   docker exec mlops-postgres pg_restore -U mlflow -d mlflow --clean --if-exists /tmp/restore.dump
#   docker compose --env-file .env start mlflow
#   curl -H 'Host: localhost:5000' 'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/get?name=gp-mof-capstone-model'

set -euo pipefail

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-mlops-postgres}"
POSTGRES_USER="${POSTGRES_USER:-mlflow}"
POSTGRES_DB="${POSTGRES_DB:-mlflow}"
BACKUP_DIR="${BACKUP_DIR:-/opt/mlops-local-poc/mlflow/data/postgres-backups}"
RETENTION_COUNT="${RETENTION_COUNT:-14}"
MIN_DUMP_BYTES="${MIN_DUMP_BYTES:-10240}"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') backup-postgres: $*"
}

mkdir -p -- "${BACKUP_DIR}"
exec 200>"${BACKUP_DIR}/backup.lock"
if ! flock -n 200; then
  log "SKIP: another backup is already running"
  exit 0
fi

if ! docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
  log "ERROR: PostgreSQL is not reachable, no dump taken, existing backups kept"
  exit 1
fi

REGISTRY_COUNT="$(docker exec "${POSTGRES_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
  "SELECT (SELECT count(*) FROM registered_models) + (SELECT count(*) FROM model_versions)" 2>/dev/null || echo "unreadable")"
if [[ "${REGISTRY_COUNT}" == "unreadable" || "${REGISTRY_COUNT}" -eq 0 ]]; then
  log "REFUSE: registry is empty or unreadable (count=${REGISTRY_COUNT}), no dump taken, existing backups kept"
  exit 1
fi

STAMP="$(date '+%Y%m%d-%H%M%S')"
TMP_NAME="mlflow-${STAMP}.dump"
log "dumping registry (models+versions=${REGISTRY_COUNT}) to ${TMP_NAME}"

docker exec "${POSTGRES_CONTAINER}" pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc -f "/tmp/${TMP_NAME}"
if ! docker exec "${POSTGRES_CONTAINER}" pg_restore --list "/tmp/${TMP_NAME}" >/dev/null 2>&1; then
  docker exec "${POSTGRES_CONTAINER}" rm -f "/tmp/${TMP_NAME}"
  log "ERROR: dump verification (pg_restore --list) failed, no file kept, existing backups kept"
  exit 1
fi
docker cp "${POSTGRES_CONTAINER}:/tmp/${TMP_NAME}" "${BACKUP_DIR}/${TMP_NAME}"
docker exec "${POSTGRES_CONTAINER}" rm -f "/tmp/${TMP_NAME}"

SIZE="$(stat -c '%s' "${BACKUP_DIR}/${TMP_NAME}")"
if [[ "${SIZE}" -lt "${MIN_DUMP_BYTES}" ]]; then
  rm -f "${BACKUP_DIR}/${TMP_NAME}"
  log "ERROR: dump too small (${SIZE} bytes), file discarded, existing backups kept"
  exit 1
fi

# Retention: keep the newest RETENTION_COUNT dumps, only after a verified dump.
mapfile -t OLD_DUMPS < <(ls -1t "${BACKUP_DIR}"/mlflow-*.dump 2>/dev/null | tail -n "+$((RETENTION_COUNT + 1))" || true)
for old in ${OLD_DUMPS[@]+"${OLD_DUMPS[@]}"}; do
  rm -f -- "${old}"
  log "rotated out $(basename "${old}")"
done

COUNT="$(ls -1 "${BACKUP_DIR}"/mlflow-*.dump 2>/dev/null | wc -l)"
log "OK: ${TMP_NAME} (${SIZE} bytes), ${COUNT} backup(s) kept"
