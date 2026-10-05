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
        └── train.py
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

`run_training.py` recupera automaticamente:

- commit Git corrente
- URL del remote Git
- stato dirty/clean della working tree

Per questo motivo il launcher deve essere eseguito da una copia reale del repository Git, cioè una directory che contenga `.git/`.

La copia runtime sotto `/opt/mlops-local-poc/training/data/` non è un checkout Git e non deve contenere il codice sorgente.

Il repository Git può essere:

- il checkout Windows accessibile da WSL, ad esempio sotto `/mnt/c/...`;
- un checkout Git completo in una directory WSL separata;
- un altro working copy Git valido.

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
```

Esempio generico:

```bash
export MLFLOW_TRACKING_URI=http://mlflow:5000
export DOCKER_NETWORK=mlops-local-network
export TRAINING_DATA_HOST_PATH=/opt/mlops-local-poc/training/data/input/SeoulBikeData.csv

python3 scripts/run_training.py
```

Esempio con parametri Random Forest diversi:

```bash
python3 scripts/run_training.py \
  --training-data /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv \
  --mlflow-tracking-uri http://mlflow:5000 \
  --docker-network mlops-local-network \
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
