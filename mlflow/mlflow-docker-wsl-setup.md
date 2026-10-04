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

I dati persistenti risiedono nei bind mount:

```text
/opt/mlops-local-poc/mlflow/data/postgres
/opt/mlops-local-poc/mlflow/data/seaweedfs
```

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

Impostare le directory sorgente e destinazione:

```bash
WINDOWS_MLFLOW_DIR='/mnt/c/<PERCORSO_REPOSITORY>/mlops-local-poc/mlflow'
DEPLOY_DIR='/opt/mlops-local-poc/mlflow'
```

Creare la directory runtime e copiare i file statici del repository:

```bash
sudo mkdir -p "$DEPLOY_DIR"

sudo rsync -a --delete \
  "$WINDOWS_MLFLOW_DIR/" \
  "$DEPLOY_DIR/"

sudo chown -R "$USER":"$USER" /opt/mlops-local-poc
```

Il comando `rsync --delete` è adatto alla prima installazione. In seguito, non deve cancellare accidentalmente file runtime e dati persistenti. Dopo la prima installazione, usare il comando seguente:

```bash
sudo rsync -a --delete \
  --exclude='.env' \
  --exclude='object-store/s3.json' \
  --exclude='data/' \
  "$WINDOWS_MLFLOW_DIR/" \
  "$DEPLOY_DIR/"
```

## Normalizzare owner, permessi e line ending

Eseguire lo script di normalizzazione di proprietario e permessi incluso nel repository:

```bash
cd /opt/mlops-local-poc/mlflow

bash tools/set-permissions.sh
```

Convertire i file di testo da CRLF a LF:

```bash
find . -type f \
  \( \
    -name '*.sh' -o \
    -name '*.yaml' -o \
    -name '*.yml' -o \
    -name '*.json' -o \
    -name '*.md' -o \
    -name '.env' -o \
    -name '.env.example' \
  \) \
  -exec sed -i 's/\r$//' {} +
```

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

## Preparare i bind mount dati

Creare esplicitamente le directory che saranno montate nei container PostgreSQL e SeaweedFS:

```bash
sudo install -d \
  -o "$USER" \
  -g "$USER" \
  -m 0755 \
  /opt/mlops-local-poc/mlflow/data/postgres \
  /opt/mlops-local-poc/mlflow/data/seaweedfs
```

Verificare le directory persistenti:

```bash
stat -c '%A %a %U:%G %n' \
  /opt/mlops-local-poc/mlflow/data \
  /opt/mlops-local-poc/mlflow/data/postgres \
  /opt/mlops-local-poc/mlflow/data/seaweedfs
```

Controllare che le directory esistano, siano di proprietà dell’utente WSL e abbiano permessi iniziali `755`.

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
  --mount type=bind,src="$(pwd)/tools/smoke_test_tracking.py",dst=/opt/mlops/smoke_test_tracking.py,readonly \
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
  --mount type=bind,src="$(pwd)/tools/verify_smoke_test_tracking.py",dst=/opt/mlops/verify_smoke_test_tracking.py,readonly \
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

## Stato finale della fase

Al termine della procedura risultano verificati:

- MLflow Tracking Server locale;
- PostgreSQL come backend store;
- SeaweedFS come object store S3-compatible;
- bucket `mlflow-artifacts`;
- artifact proxy MLflow;
- persistenza PostgreSQL e SeaweedFS nei bind mount sotto `/opt`;
- ciclo Compose `stop` → `start`;
- tracking tecnico end-to-end di parametri, tag e artefatti;
- recupero di un artefatto tramite MLflow dopo restart.

