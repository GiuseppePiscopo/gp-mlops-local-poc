# Training gp-mof-capstone: containerizzazione e registrazione del modello in MLflow

Questa procedura sincronizza in WSL2 il componente di training del capstone, costruisce l'immagine Docker, esegue due training containerizzati di un `RandomForestRegressor` sul dataset Seoul Bike Sharing e ne registra le versioni nel Model Registry MLflow, inclusi assegnazione degli alias e verifica del recupero del modello.

Il repository Git Windows contiene codice, Dockerfile, script e dataset di esempio. La directory `/opt/mlops-local-poc/training` in WSL contiene la copia operativa degli script e il dataset runtime, mai copiato nell'immagine Docker né committato nel repository.

Questa procedura presuppone uno stack MLflow già installato e funzionante, raggiungibile nella rete Docker `mlops-local-network` (vedi `mlflow/mlflow-docker-wsl-setup.md`). Non copre l'integrazione con KServe, trattata in una procedura dedicata.

## Prerequisiti

- Windows con WSL2 e distribuzione Ubuntu
- Docker Desktop configurato con integrazione WSL
- stack MLflow attivo nella rete Docker `mlops-local-network` (Tracking Server, PostgreSQL, SeaweedFS)
- repository Windows `mlops-local-poc` completo della cartella `training/` e di `data/sample/SeoulBikeData.csv`
- Git LFS installato, con `git lfs pull` già eseguito nel repository (il CSV del dataset è tracciato con Git LFS)
- accesso `sudo` nell'utente Ubuntu WSL
- `rsync`, `sed`, `find`, `curl` disponibili in WSL
- `python3` (>= 3.11) e Git disponibili in WSL; repository sorgente pulito e
  completo di `ci-cd/scripts/prepare_image.py`.

## Verificare la raggiungibilità di MLflow

Verificare che un container temporaneo nella rete `mlops-local-network` risolva il DNS interno `mlflow` e raggiunga il Tracking Server.

```bash
docker run --rm \
  --network mlops-local-network \
  python:3.11-slim \
  python -c "from urllib.request import urlopen; print(urlopen('http://mlflow:5000/version').read().decode())"
```

L'output atteso è la versione del server, per esempio:

```text
3.14.0
```

## Copiare `training/` in WSL

Sincronizzare dal repository Windows verso la copia operativa WSL solo i sorgenti statici richiesti dal build Docker (`src/`, `scripts/` e i file di root), lasciando intatto il dataset runtime.

```bash
cd /mnt/c/<percorso-repository>/mlops-local-poc/training

bash scripts/sync-to-wsl.sh
```

Verificare l'elenco dei file sincronizzati:

```bash
find /opt/mlops-local-poc/training -maxdepth 3 -type f -printf '%P\n' | sort
```

L'output atteso include almeno:

```text
.dockerignore
.gitignore
Dockerfile
requirements.txt
scripts/build-image.sh
scripts/run_training.py
scripts/set-permissions.sh
scripts/sync-to-wsl.sh
src/common/__init__.py
src/common/training_utils.py
src/random_forest/__init__.py
src/random_forest/train.py
src/random_forest/verify_registered_model.py
```

## Normalizzare permessi e proprietario

Ripristinare proprietario e permessi coerenti sull'intero perimetro runtime `/opt/mlops-local-poc`, utile dopo operazioni eseguite con `sudo`.

```bash
cd /opt/mlops-local-poc/training

bash scripts/set-permissions.sh
```

Verificare i permessi di uno script eseguibile:

```bash
stat -c '%U:%G %a %n' /opt/mlops-local-poc/training/scripts/run_training.py
```

Controllare che il proprietario sia il proprio utente WSL e che i permessi siano `755`.

## Preparare il dataset

Creare la directory runtime del dataset e copiare il CSV di esempio dal repository, separandolo definitivamente dal codice sincronizzato. Sostituire `<percorso-repository>` con il percorso WSL del repository Windows.

```bash
sudo install -d -o "$USER" -g "$USER" -m 0755 \
  /opt/mlops-local-poc/training/data/input

cp "/mnt/c/<percorso-repository>/mlops-local-poc/data/sample/SeoulBikeData.csv" \
  /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv
```

Verificare presenza, dimensione e hash del file:

```bash
ls -lh /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv

sha256sum /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv
```

Controllare che il file esista e che lo SHA-256 resti stabile tra esecuzioni successive di questo stesso passo.

## Costruire e verificare l'immagine Docker

Impostare il percorso del repository Git Windows e il tag. Il repository
deve essere pulito, senza modifiche staged, unstaged o file non tracciati.
Mantenere le variabili esportate per i comandi successivi.

```bash
export GIT_REPOSITORY="/mnt/c/<percorso-repository>/mlops-local-poc"
export IMAGE_TAG="gp-mof-capstone-training:git-$(git -C "$GIT_REPOSITORY" rev-parse HEAD)"
```

Costruire l'immagine di training dal build context WSL.

```bash
cd /opt/mlops-local-poc/training

./scripts/build-image.sh
```

Controllare nell'output JSON `image`, `image_id` e `git_commit`.

Verificare l'immagine:

```bash
docker image inspect \
  "$IMAGE_TAG" \
  --format 'image_id={{.Id}} created={{.Created}}'
```

L'output atteso è:

```text
image_id=sha256:...
```

Verificare l'entrypoint senza avviare alcun run MLflow:

```bash
docker run --rm \
  "$IMAGE_TAG" \
  --help
```

Controllare che compaiano almeno questi argomenti, senza `ModuleNotFoundError` o `ImportError`:

```text
--training_data
--n_estimators
--max_depth
--registered_model_name
--fail_for_alert_test
```

Non cambiare commit tra build e training. Per ricostruire lo stesso commit,
usare un nuovo suffisso `-rebuild1` in `IMAGE_TAG`, sia in build sia in avvio.

## Eseguire il primo training (Version 1)

Avviare il primo run reale end-to-end. Sostituire `<percorso-repository>` con il percorso WSL del repository Windows.

```bash
cd /opt/mlops-local-poc/training

./scripts/run_training.py \
  --training-data /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv \
  --mlflow-tracking-uri http://mlflow:5000 \
  --docker-network mlops-local-network \
  --git-repository /mnt/c/<percorso-repository>/mlops-local-poc \
  --n-estimators 100 \
  --max-depth 10
```

L'output atteso include, dopo `Training configuration` e `Docker command`:

```text
Reading data...
Preparing data...
Training model...
MAE: ...
RMSE: ...
R2: ...
MAPE: ...
...
Successfully registered model 'gp-mof-capstone-model'.
Created version '1' of model 'gp-mof-capstone-model'.
```

Verificare la versione registrata tramite l'API REST di MLflow. L'header `Host` è necessario perché il Tracking Server è avviato con `--allowed-hosts localhost:*,mlflow:5000`.

```bash
curl --fail --silent --show-error \
  -H 'Host: localhost:5000' \
  'http://127.0.0.1:5000/api/2.0/mlflow/model-versions/get?name=gp-mof-capstone-model&version=1'
```

Controllare che la risposta contenga:

```text
"version": "1"
"status": "READY"
```

## Eseguire un secondo training con iperparametri diversi (Version 2)

Eseguire un secondo run cambiando solo gli iperparametri del Random Forest, a parità di dataset, seed e split, per ottenere una seconda versione comparabile.

```bash
cd /opt/mlops-local-poc/training

./scripts/run_training.py \
  --training-data /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv \
  --mlflow-tracking-uri http://mlflow:5000 \
  --docker-network mlops-local-network \
  --git-repository /mnt/c/<percorso-repository>/mlops-local-poc \
  --n-estimators 300 \
  --max-depth 20
```

L'output atteso include:

```text
Successfully registered model 'gp-mof-capstone-model'.
Created version '2' of model 'gp-mof-capstone-model'.
```

Verificare la seconda versione:

```bash
curl --fail --silent --show-error \
  -H 'Host: localhost:5000' \
  'http://127.0.0.1:5000/api/2.0/mlflow/model-versions/get?name=gp-mof-capstone-model&version=2'
```

Controllare che la risposta contenga `"version": "2"` e `"status": "READY"`, quindi confrontare le metriche `mae`, `rmse`, `r2` e `mape` stampate dai due run per decidere quale versione promuovere.

## Assegnare gli alias alle model version

Registrare nel Model Registry quale versione è riferimento storico (`baseline`), quale è attualmente approvata (`champion`) e quale è candidata alla promozione (`candidate`). Gli esempi seguenti assumono che Version 1 resti `baseline`/`champion` e Version 2 sia `candidate`; adattare i valori `version` all'esito del confronto delle metriche.

```bash
curl --fail --silent --show-error \
  -X POST \
  -H 'Host: localhost:5000' \
  -H 'Content-Type: application/json' \
  -d '{"name":"gp-mof-capstone-model","alias":"baseline","version":"1"}' \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/alias'

curl --fail --silent --show-error \
  -X POST \
  -H 'Host: localhost:5000' \
  -H 'Content-Type: application/json' \
  -d '{"name":"gp-mof-capstone-model","alias":"champion","version":"1"}' \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/alias'

curl --fail --silent --show-error \
  -X POST \
  -H 'Host: localhost:5000' \
  -H 'Content-Type: application/json' \
  -d '{"name":"gp-mof-capstone-model","alias":"candidate","version":"2"}' \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/alias'
```

Ogni chiamata risponde con:

```text
{}
```

Verificare l'elenco completo degli alias:

```bash
curl --fail --silent --show-error \
  -H 'Host: localhost:5000' \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/get?name=gp-mof-capstone-model'
```

Controllare che la sezione `aliases` della risposta contenga:

```text
{"alias": "baseline", "version": "1"}
{"alias": "candidate", "version": "2"}
{"alias": "champion", "version": "1"}
```

## Verificare il recupero del modello dal Registry

Eseguire un client nuovo che risolve un alias, scarica il modello e calcola alcune predizioni, a conferma che la versione referenziata sia realmente recuperabile e utilizzabile.

```bash
docker run --rm \
  --network mlops-local-network \
  -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
  -v /opt/mlops-local-poc/training/data:/data:ro \
  --entrypoint python \
  "$IMAGE_TAG" \
  -m random_forest.verify_registered_model \
  --training_data /data/input/SeoulBikeData.csv \
  --model_uri models:/gp-mof-capstone-model@candidate \
  --rows 5
```

L'output atteso è:

```text
Model URI: models:/gp-mof-capstone-model@candidate
Input rows: 5
Predictions:
  row=1 prediction=...
  ...
```

## Stato finale della fase

Al termine della procedura risultano verificati:

- immagine Docker `gp-mof-capstone-training:git-<SHA>` costruita dallo snapshot
  committato, con image ID locale e label di provenance verificabili;
- entrypoint del container funzionante (import dei package, CLI);
- due training containerizzati completati, con parametri, metriche e provenance Git registrati su MLflow;
- due model version (`1` e `2`) in stato `READY` nel Model Registry `gp-mof-capstone-model`;
- alias `baseline`, `champion` e `candidate` assegnati alle rispettive versioni;
- recupero end-to-end di una model version dal Registry tramite alias, con predizione eseguita con successo.
