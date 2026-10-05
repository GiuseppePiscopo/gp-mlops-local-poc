# GP MOF Capstone training

Container di training locale per il capstone MLOps Fundamentals.

Il training usa il dataset Seoul Bike Sharing, addestra un `RandomForestRegressor`, registra il run su MLflow e crea una nuova versione del modello nel Model Registry.

## Componenti

Il training containerizzato usa:

- MLflow Tracking Server per experiment tracking e Model Registry
- PostgreSQL, gestito dal Tracking Server, per metadata di experiment, run e model version
- SeaweedFS S3-compatible, gestito dal Tracking Server, per artefatti e modelli
- Docker per il runtime del training
- Git per la provenance del codice
- un CSV Seoul Bike Sharing montato in sola lettura nel container

Il container di training conosce solo `MLFLOW_TRACKING_URI`.

Il container non riceve endpoint, access key o secret key di SeaweedFS.

## Identificativi

| Elemento | Valore |
|---|---|
| MLflow experiment | `gp-mof-capstone-training` |
| Registered model | `gp-mof-capstone-model` |
| Docker image di sviluppo | `gp-mof-capstone-training:dev` |
| Dataset identifier tag | `dataset.name` |
| Dataset identifier value | `gp-mof-capstone-data` |
| Dataset container path | `/data/input/SeoulBikeData.csv` |

## Struttura

```text
training/
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
├── requirements.txt
├── scripts/
│   ├── build-image.sh
│   └── run_training.py
└── src/
    ├── common/
    │   ├── __init__.py
    │   └── training_utils.py
    └── random_forest/
        ├── __init__.py
        ├── train.py
        └── verify_registered_model.py
```

## Confini filesystem

Il repository Git è l'origine versionata di codice, Dockerfile, dipendenze, script e documentazione.

Il runtime WSL sotto `/opt/mlops-local-poc/` contiene esclusivamente configurazione runtime Docker, dati persistenti e dati montati nei container.

Il CSV non viene copiato nell'immagine Docker e non viene committato nel repository Git.

Struttura runtime prevista:

```text
/opt/mlops-local-poc/
└── training/
    └── data/
        └── input/
            └── SeoulBikeData.csv
```

Il CSV viene montato nel container come file read-only:

```text
WSL host:
  /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv

Training container:
  /data/input/SeoulBikeData.csv
```

## Provenance Git

La copia Linux sotto `/opt/mlops-local-poc/training/` è un build workspace e non contiene il repository Git completo.

Il repository Git autorevole resta il checkout Windows/Bitbucket, normalmente accessibile da WSL sotto `/mnt/c/...`.

Il launcher riceve esplicitamente il percorso del repository autorevole tramite:

```text
--git-repository
```

oppure tramite la variabile d'ambiente:

```text
GIT_REPOSITORY
```

Prima di eseguire Docker, il launcher verifica che il percorso sia una working tree Git valida e rileva:

- commit Git corrente;
- URL del remote `origin`;
- stato `dirty` o `clean` della working tree.

Questi valori vengono inoltrati al training container e registrati come MLflow tags.

Il launcher non accetta run privi di provenance Git valida.

## Build dell'immagine

Lo script Bash di build deve essere eseguito da WSL, nella directory `training/` del repository Git:

```bash
./scripts/build-image.sh
```

L'immagine risultante è:

```text
gp-mof-capstone-training:dev
```

Per usare un tag diverso:

```bash
IMAGE_TAG=gp-mof-capstone-training:0.1.0 ./scripts/build-image.sh
```

## Esecuzione del training

Il launcher Python riceve la configurazione runtime tramite argomenti CLI o environment variables.

Variabili obbligatorie:

```text
MLFLOW_TRACKING_URI
DOCKER_NETWORK
TRAINING_DATA_HOST_PATH
GIT_REPOSITORY
```

Esempio generico:

```bash
export MLFLOW_TRACKING_URI=http://mlflow:5000
export DOCKER_NETWORK=mlops-local-network
export TRAINING_DATA_HOST_PATH=/opt/mlops-local-poc/training/data/input/SeoulBikeData.csv
export GIT_REPOSITORY=/mnt/c/<percorso-del-repository>/mlops-local-poc

python3 scripts/run_training.py
```

Esempio con parametri Random Forest diversi:

```bash
python3 scripts/run_training.py \
  --training-data /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv \
  --mlflow-tracking-uri http://mlflow:5000 \
  --docker-network mlops-local-network \
  --git-repository /mnt/c/<percorso-del-repository>/mlops-local-poc \
  --n-estimators 300 \
  --max-depth 20
```

Il launcher:

1. verifica che il CSV esista;
2. verifica che sia un file `.csv`;
3. verifica che l'immagine Docker esista;
4. legge commit, remote e stato Git;
5. determina il digest dell'immagine, se disponibile;
6. stampa il comando Docker completo;
7. avvia il training container.

## Parametri training

| Parametro | Default |
|---|---:|
| `n_estimators` | `100` |
| `max_depth` | `10` |
| random seed | `42` |
| test size | `0.20` |

Il secondo run può cambiare almeno `n_estimators` e/o `max_depth` per produrre una nuova versione comparabile del modello registrato.

## Dati registrati in MLflow

Ogni run registra:

- parametri del modello
- seed e test split
- metriche `mae`, `rmse`, `r2` e `mape`
- feature name dopo il preprocessing
- input example
- model signature
- modello scikit-learn
- file e metadata del dataset
- SHA-256 del CSV
- dimensione e ultima modifica UTC del CSV
- commit Git
- remote Git
- stato dirty/clean del repository
- tag e digest dell'immagine Docker, quando disponibile

Il modello viene registrato in MLflow Model Registry con il nome:

```text
gp-mof-capstone-model
```

Ogni training completato con successo produce una nuova model version.

## Verifica del modello registrato

`src/random_forest/verify_registered_model.py` è uno smoke test che dimostra il recupero end-to-end di una model version dal Registry: risolve un alias MLflow (es. `@candidate` o `@champion`), scarica l'artefatto dall'object storage, lo carica con `mlflow.sklearn.load_model()` ed esegue una predizione su alcune righe del CSV.

A differenza del training, il verifier scarica l'artefatto del modello direttamente dall'object storage S3-compatible, senza passare dal proxy del Tracking Server. Per questo il container riceve anche l'endpoint e le credenziali di SeaweedFS, non solo `MLFLOW_TRACKING_URI`.

Esempio di esecuzione, con la stessa rete Docker, gli stessi endpoint MLflow/S3 e lo stesso mount dati usati nel training:

```bash
docker run --rm \
  --network mlops-local-network \
  -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
  -e MLFLOW_S3_ENDPOINT_URL=http://seaweedfs:8333 \
  -e AWS_ACCESS_KEY_ID=admin \
  -e AWS_SECRET_ACCESS_KEY=admin \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -v /opt/mlops-local-poc/training/data:/data:ro \
  --entrypoint python \
  gp-mof-capstone-training:dev \
  -m random_forest.verify_registered_model \
  --training_data /data/input/SeoulBikeData.csv \
  --model_uri models:/gp-mof-capstone-model@candidate \
  --rows 5
```

Argomenti:

| Argomento | Obbligatorio | Default |
|---|---|---|
| `--training_data` | sì | — |
| `--model_uri` | no | `models:/gp-mof-capstone-model@candidate` |
| `--rows` | no | `5` |

L'output atteso elenca l'URI del modello risolto e una predizione per ciascuna delle righe richieste, a conferma che la model version referenziata dall'alias è realmente recuperabile e utilizzabile.
