# MLflow locale con PostgreSQL e SeaweedFS su WSL2

Questa procedura installa e verifica un ambiente locale di MLflow Tracking Server con PostgreSQL come backend store e SeaweedFS come artifact store S3-compatible, eseguito con Docker Compose in WSL2.

L’obiettivo di questa fase è preparare e validare l’infrastruttura di experiment tracking e artifact storage. Non include training reale, logging di modelli, Model Registry, integrazione KServe o deployment Kubernetes.

## Architettura

Lo stack locale contiene tre servizi Docker Compose:

```text
MLflow client / browser
        |
        v
MLflow Tracking Server
        |
        +--> PostgreSQL
        |      experiment, run, parametri, metriche, tag,
        |      registered model e metadata delle versioni
        |
        +--> SeaweedFS S3-compatible
               modelli, artefatti, file di output e altri file dei run
```

La directory runtime WSL usata da questa procedura è:

```text
/opt/mlops-local-poc/mlflow
```

I dati persistenti risiedono nel named volume Docker `mlops-local-pgdata`
(PostgreSQL) e nel bind mount SeaweedFS:

```text
named volume mlops-local-pgdata -> /var/lib/postgresql/data (PostgreSQL)
/opt/mlops-local-poc/mlflow/data/seaweedfs (SeaweedFS)
```

PostgreSQL usa un named volume nella VM Docker (al riparo dall'`initdb` che
azzera il registry dopo un avvio difettoso di Docker Desktop); SeaweedFS resta
su bind mount.

Il repository Windows resta invece la sorgente versionata di manifest, template, documentazione e strumenti.

## Prerequisiti

Prima di iniziare devono essere disponibili:

- Windows con WSL2 e distribuzione Ubuntu
- Docker Desktop configurato con integrazione WSL
- Docker Engine e Docker Compose plugin disponibili dalla shell Ubuntu
- repository Windows `mlops-local-poc` completo dei file statici della fase MLflow
- accesso `sudo` nell’utente Ubuntu WSL
- `openssl`, `sed`, `find`, `grep` e `rsync` disponibili in WSL

Il file Compose espone i servizi soltanto sul loopback WSL:

| Servizio | Indirizzo | Porta predefinita | Uso |
|---|---|---:|---|
| PostgreSQL | `127.0.0.1` | `5432` | Debug SQL con `psql`, DBeaver o DataGrip |
| MLflow | `127.0.0.1` | `5000` | UI web, Tracking API e artifact proxy |
| SeaweedFS S3 API | `127.0.0.1` | `8333` | Diagnostica e amministrazione S3 locale |
| SeaweedFS master | `127.0.0.1` | `9333` | Diagnostica SeaweedFS |
| SeaweedFS filer | `127.0.0.1` | `8888` | Diagnostica SeaweedFS |

## Copiare il repository in WSL

Le operazioni Docker Compose e i bind mount devono essere eseguiti dal filesystem Linux WSL, non da `/mnt/c/...`.

Sincronizzare i file statici dal repository con lo script dedicato (stessa
tecnica degli altri lab). Alla prima installazione creare prima la directory
runtime e sistemarne il proprietario:

```bash
sudo install -d -o "$USER" -g "$USER" -m 0755 /opt/mlops-local-poc/mlflow

cd /mnt/c/<PERCORSO_REPOSITORY>/mlops-local-poc/mlflow

bash scripts/sync-to-wsl.sh
```

Ai re-sync successivi basta rieseguire lo script: sincronizza solo le sorgenti
statiche (`scripts/`, `object-store/` senza `s3.json`, `compose.yaml`,
`.env.example`, `.gitignore`) e non tocca mai `.env`, `object-store/s3.json`
né `data/`.

## Creare i file runtime con segreti

Creare le copie runtime non versionate:

```bash
cd /opt/mlops-local-poc/mlflow

cp .env.example .env
cp object-store/s3.json.example object-store/s3.json
```

Generare password e chiave S3 locali. I valori esadecimali evitano caratteri che richiederebbero URL encoding nell’URI PostgreSQL di MLflow.

```bash
cd /opt/mlops-local-poc/mlflow

POSTGRES_PASSWORD_VALUE="$(openssl rand -hex 24)"
S3_SECRET_VALUE="$(openssl rand -hex 32)"

sed -i \
  "s|replace-with-a-local-postgres-password|${POSTGRES_PASSWORD_VALUE}|" \
  .env

sed -i \
  "s|replace-with-a-local-s3-secret|${S3_SECRET_VALUE}|" \
  .env

sed -i \
  "s|replace-with-the-same-value-used-for-S3_SECRET_ACCESS_KEY|${S3_SECRET_VALUE}|" \
  object-store/s3.json

chmod 600 .env object-store/s3.json

unset POSTGRES_PASSWORD_VALUE S3_SECRET_VALUE
```

Verificare senza stampare i segreti:

```bash
grep -E '^(POSTGRES_PASSWORD|S3_SECRET_ACCESS_KEY)=' .env \
  | sed 's/=.*$/=<redacted>/'

grep -E '"(accessKey|secretKey)"' object-store/s3.json \
  | sed 's/"secretKey": ".*"/"secretKey": "<redacted>"/'

stat -c '%a %n' .env object-store/s3.json
```

Controllare che:

```text
POSTGRES_PASSWORD=<redacted>
S3_SECRET_ACCESS_KEY=<redacted>
```

e:

```text
600 .env
600 object-store/s3.json
```

## Preparare la directory dati SeaweedFS

Creare esplicitamente la directory che sarà montata nel container SeaweedFS.
PostgreSQL non richiede questo passo: il named volume `mlops-local-pgdata`
dichiarato in `compose.yaml` viene creato automaticamente da Docker al primo
`docker compose up`.

```bash
sudo install -d \
  -o "$USER" \
  -g "$USER" \
  -m 0755 \
  /opt/mlops-local-poc/mlflow/data/seaweedfs
```

Verificare la directory persistente:

```bash
stat -c '%A %a %U:%G %n' \
  /opt/mlops-local-poc/mlflow/data \
  /opt/mlops-local-poc/mlflow/data/seaweedfs
```

Controllare che la directory esista, sia di proprietà dell’utente WSL e abbia permessi iniziali `755`. Verificare inoltre il volume PostgreSQL dopo il primo avvio:

```bash
docker volume inspect mlops-local-pgdata --format 'volume={{.Name}}'
```

## Validare Docker Compose

Validare la configurazione Compose prima di scaricare immagini o avviare servizi:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env config --quiet
echo $?
```

L’output atteso è:

```text
0
```

## Scaricare le immagini

Scaricare le immagini definite in `compose.yaml`, senza avviare container:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env pull
```

Verificare le immagini locali:

```bash
docker image ls \
  --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}\t{{.Size}}' \
  | grep -E '^(REPOSITORY|postgres|chrislusf/seaweedfs|ghcr.io/mlflow/mlflow)'
```

Controllare la presenza delle immagini PostgreSQL, SeaweedFS e MLflow.

## Avviare lo stack

Avviare PostgreSQL, SeaweedFS e MLflow, attendendo il completamento degli healthcheck:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env up \
  --detach \
  --wait \
  --wait-timeout 120
```

Verificare lo stato dei container:

```bash
docker compose --env-file .env ps
```

Controllare che:

```text
mlops-postgres    Up ... (healthy)
mlops-seaweedfs   Up ... (healthy)
mlops-mlflow      Up ...
```

## Verificare il Tracking Server e PostgreSQL

Verificare la risposta HTTP del server MLflow:

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:5000/health
```

L’output atteso è:

```text
OK
```

Verificare che MLflow abbia inizializzato il proprio schema nel backend store PostgreSQL:

```bash
docker compose --env-file .env exec -T postgres \
  sh -ec 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    SELECT current_database() AS database, current_user AS database_user;
    SELECT version_num AS mlflow_schema_revision FROM alembic_version;
  "'
```

Controllare che:

- database e utente siano `mlflow`;
- `alembic_version` restituisca una revisione non vuota.

## Creare il bucket artifact store

Creare il bucket S3-compatible dedicato agli artefatti MLflow. Il container AWS CLI è temporaneo e usa la rete Docker interna; non contatta AWS.

```bash
cd /opt/mlops-local-poc/mlflow

set -a
. ./.env
set +a

export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="$S3_REGION"

docker run --rm \
  --network mlops-local-network \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.37.7 \
  --endpoint-url http://seaweedfs:8333 \
  s3api create-bucket \
  --bucket "$S3_BUCKET"
```

Verificare l’esistenza del bucket:

```bash
docker run --rm \
  --network mlops-local-network \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.37.7 \
  --endpoint-url http://seaweedfs:8333 \
  s3api list-buckets \
  --query 'Buckets[].Name' \
  --output text
```

L’output atteso è:

```text
mlflow-artifacts
```

Pulire le variabili dalla shell:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST_PORT
unset MLFLOW_HOST_PORT S3_BUCKET S3_REGION S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY
unset S3_HOST_PORT SEAWEEDFS_MASTER_HOST_PORT SEAWEEDFS_FILER_HOST_PORT
```

## Eseguire lo smoke test tecnico

Lo smoke test verifica Tracking Server, PostgreSQL, artifact proxy MLflow e SeaweedFS senza eseguire training reale. Crea un finto esperimento, un run, parametri, tag e un file come artefatto. 

Eseguire lo script tramite un client MLflow temporaneo:

```bash
cd /opt/mlops-local-poc/mlflow

docker run --rm \
  --network mlops-local-network \
  -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
  --mount type=bind,src="$(pwd)/scripts/smoke_test_tracking.py",dst=/opt/mlops/smoke_test_tracking.py,readonly \
  --entrypoint python \
  ghcr.io/mlflow/mlflow:v3.14.0-full \
  /opt/mlops/smoke_test_tracking.py
```

Controllare un output analogo a:

```text
MLFLOW_SMOKE_EXPERIMENT_ID=<experiment-id>
MLFLOW_SMOKE_RUN_ID=<run-id>
MLFLOW_SMOKE_ARTIFACT=smoke-test.txt
```

Il warning Git eventualmente emesso dal client tecnico è atteso.

Conservare il valore di `MLFLOW_SMOKE_RUN_ID` per le verifiche successive.

## Verificare l’artefatto nel bucket

Verificare che l’artefatto creato dallo smoke test esista fisicamente nel bucket.

Sostituire `<RUN_ID>` con il run ID prodotto dal test.

```bash
cd /opt/mlops-local-poc/mlflow

set -a
. ./.env
set +a

export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="$S3_REGION"

docker run --rm \
  --network mlops-local-network \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.37.7 \
  --endpoint-url http://seaweedfs:8333 \
  s3api list-objects-v2 \
  --bucket "$S3_BUCKET" \
  --query 'Contents[].Key' \
  --output text \
  | tr '\t' '\n' \
  | grep -F "$MLFLOW_SMOKE_RUN_ID"
```

Controllare una chiave che termini con:

```text
/artifacts/smoke-test.txt
```

La struttura generale attesa è:

```text
<experiment-id>/<run-id>/artifacts/smoke-test.txt
```

Salvare la chiave completa restituita, senza includere il nome del bucket. Per esempio:

```text
1/8a2f...c91/artifacts/smoke-test.txt
```

Leggere direttamente l’oggetto dal bucket. Sostituire `<OBJECT_KEY>` con l’intera chiave ottenuta:

```bash
S3_OBJECT_KEY='<OBJECT_KEY>'

docker run --rm \
  --network mlops-local-network \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_DEFAULT_REGION \
  amazon/aws-cli:2.37.7 \
  --endpoint-url http://seaweedfs:8333 \
  s3 cp "s3://${S3_BUCKET}/${S3_OBJECT_KEY}" -
```

L’output atteso è:

```text
MLflow artifact proxy to local S3-compatible storage: OK
```

Pulire le variabili:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST_PORT
unset MLFLOW_HOST_PORT S3_BUCKET S3_REGION S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY
unset S3_HOST_PORT SEAWEEDFS_MASTER_HOST_PORT SEAWEEDFS_FILER_HOST_PORT
unset S3_OBJECT_KEY
```

## Verificare persistenza e restart

Verificare che dati e configurazione sopravvivano al ciclo standard di arresto e riavvio dello stack:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env stop

docker compose --env-file .env start
```

Dopo circa 10 secondi, verificare:

```bash
docker compose --env-file .env ps
```

Controllare che PostgreSQL e SeaweedFS siano healthy e che MLflow risponda:

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:5000/health
```

L’output atteso è:

```text
OK
```

## Verificare il run dopo restart

La verifica legge il run tecnico già esistente, verifica parametro e tag, quindi scarica `smoke-test.txt` tramite l’artifact proxy MLflow. Non crea nuovi run, parametri, tag o artefatti.

Sostituire `<RUN_ID>` con il run ID prodotto dallo smoke test iniziale:

```bash
cd /opt/mlops-local-poc/mlflow

docker run --rm \
  --network mlops-local-network \
  -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
  -e MLFLOW_SMOKE_RUN_ID="$MLFLOW_SMOKE_RUN_ID" \
  --mount type=bind,src="$(pwd)/scripts/verify_smoke_test_tracking.py",dst=/opt/mlops/verify_smoke_test_tracking.py,readonly \
  --entrypoint python \
  ghcr.io/mlflow/mlflow:v3.14.0-full \
  /opt/mlops/verify_smoke_test_tracking.py
```

Controllare un output analogo a:

```text
MLFLOW_SMOKE_RUN_EXISTS=<run-id>
MLFLOW_SMOKE_EXPERIMENT_ID=<experiment-id>
MLFLOW_SMOKE_ARTIFACT_READ_OK=/tmp/...
```

Il path `/tmp/...` è interno al container client temporaneo. L’esito importante è che il run, i metadata e l’artefatto siano ancora leggibili dopo il ciclo `stop` → `start`.

## Operazioni quotidiane

Avviare lo stack:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env up --detach --wait --wait-timeout 120
```

Verificare lo stato:

```bash
docker compose --env-file .env ps
```

Seguire i log:

```bash
docker compose --env-file .env logs --follow
```

Fermare lo stack mantenendo container e dati:

```bash
docker compose --env-file .env stop
```

Riavviare i container già esistenti:

```bash
docker compose --env-file .env start
```

## Backup periodico del registry

Come seconda rete, prima di chiudere il lab attivare il backup periodico con
`scripts/backup-postgres.sh` (arriva in WSL con la sincronizzazione sopra,
già con i permessi giusti): dump in `data/postgres-backups/`, solo WSL.

Eseguire il primo dump manuale e controllare che riesca:

```bash
/opt/mlops-local-poc/mlflow/scripts/backup-postgres.sh

ls -lh /opt/mlops-local-poc/mlflow/data/postgres-backups/
```

L'output deve terminare con `OK:` e la directory deve contenere il file
`mlflow-<data>-<ora>.dump`.

Schedulazione via cron (cron gira via systemd anche a terminale chiuso).
Aggiungere con `crontab -e`:

```text
*/30 * * * * /opt/mlops-local-poc/mlflow/scripts/backup-postgres.sh >> /opt/mlops-local-poc/mlflow/data/postgres-backups/cron.log 2>&1
```

Verificare che la riga sia registrata:

```bash
crontab -l
```

## Restore del registry da un dump (solo se necessario)

Solo in caso di perdita o corruzione del registry. Fermare solo `mlflow`,
PostgreSQL resta attivo:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env stop mlflow

docker cp data/postgres-backups/<file>.dump mlops-postgres:/tmp/restore.dump

docker exec mlops-postgres pg_restore -U mlflow -d mlflow --clean --if-exists /tmp/restore.dump

docker compose --env-file .env start mlflow

curl --fail --silent --show-error \
  -H 'Host: localhost:5000' \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/get?name=gp-mof-capstone-model'
```

Controllare che la risposta elenchi modello registrato, versioni e alias
attesi, poi ripetere le verifiche di tracking e inferenza dei lab a valle.

## Stato finale della fase

Al termine della procedura risultano verificati:

- MLflow Tracking Server locale;
- PostgreSQL come backend store;
- SeaweedFS come object store S3-compatible;
- bucket `mlflow-artifacts`;
- artifact proxy MLflow;
- persistenza PostgreSQL nel named volume `mlops-local-pgdata` e SeaweedFS nel bind mount sotto `/opt`;
- ciclo Compose `stop` → `start`;
- backup periodico del registry configurato (primo dump verificato, cron attiva);
- tracking tecnico end-to-end di parametri, tag e artefatti;
- recupero di un artefatto tramite MLflow dopo restart.

