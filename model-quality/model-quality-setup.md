# Monitoring della qualità del modello

Questa procedura implementa il monitoring non supervisionato della qualità
del modello `gp-mof-capstone-model`: export dei log di inferenza su SeaweedFS,
reference dataset versionato, calcolo del drift (PSI) su feature e predizioni,
export delle metriche in Prometheus e dashboard Grafana.

La qualità effettiva rispetto ai ground truth non è coperta: non esiste un
flusso di label reali.

## Prerequisiti

- WSL2 con Docker Desktop integrato e Docker Compose funzionante;
- `kind`, `kubectl`, `curl`, `docker` e `python3` disponibili in WSL;
- accesso `sudo` e directory `/opt/mlops-local-poc/` posseduta dall'utente WSL;
- cluster `mlops-test`, contesto `kind-mlops-test`;
- [request/response logging](../inference-logging/request-response-logging.md)
  completato: predictor con runtime custom e record JSONL nei log;
- Pushgateway installato nel namespace `monitoring`
  (`prometheus/config/values-pushgateway.yaml`);
- repository completo della directory `model-quality/`.

Tutti i comandi si eseguono in un terminale **Bash in WSL**, salvo dove è
indicato un secondo terminale dedicato ai port-forward.

## Preparare la directory operativa e sincronizzare

```bash
export REPO="/mnt/c/<percorso-repository>/mlops-local-poc"
ls -d "${REPO}/model-quality"

sudo install -d -o "$USER" -g "$(id -gn)" -m 0755 \
  /opt/mlops-local-poc/model-quality

bash "${REPO}/model-quality/scripts/sync-to-wsl.sh"
```

Il comando `ls -d` deve stampare il percorso, senza errori.

```bash
find /opt/mlops-local-poc/model-quality -maxdepth 2 -type f | sort
```

Devono essere presenti almeno:

```text
/opt/mlops-local-poc/model-quality/Dockerfile
/opt/mlops-local-poc/model-quality/config/export.env
/opt/mlops-local-poc/model-quality/requirements.txt
/opt/mlops-local-poc/model-quality/dashboards/model-quality-drift-dashboard.json
/opt/mlops-local-poc/model-quality/manifests/10-serviceaccount.yaml
/opt/mlops-local-poc/model-quality/manifests/11-log-reader-rbac.yaml
/opt/mlops-local-poc/model-quality/manifests/20-drift-cronjob.yaml
/opt/mlops-local-poc/model-quality/scripts/apply-dashboard.sh
/opt/mlops-local-poc/model-quality/scripts/apply-s3-secret.sh
/opt/mlops-local-poc/model-quality/scripts/build-reference.py
/opt/mlops-local-poc/model-quality/scripts/build-reference.sh
/opt/mlops-local-poc/model-quality/scripts/compute-drift.py
/opt/mlops-local-poc/model-quality/scripts/compute-drift.sh
/opt/mlops-local-poc/model-quality/scripts/filter-log-records.py
/opt/mlops-local-poc/model-quality/scripts/push-drift-metrics.sh
/opt/mlops-local-poc/model-quality/scripts/render-drift-metrics.py
/opt/mlops-local-poc/model-quality/scripts/run-drift-batch.py
```

Lo stack MLflow/PostgreSQL/SeaweedFS deve essere attivo:

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env ps

curl --fail --silent --show-error http://127.0.0.1:5000/health && echo
```

Il predictor deve essere in esecuzione:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get pods -l 'app=isvc.gp-mof-capstone-model-predictor' \
  --field-selector=status.phase=Running
```

## Avviare i port-forward

Nel **terminale dei port-forward** (WSL), lasciare attivi:

```bash
ENVOY_SVC=$(kubectl --context kind-mlops-test get svc -A \
  -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway \
  -o jsonpath='{.items[0].metadata.namespace}/{.items[0].metadata.name}')

kubectl --context kind-mlops-test -n "${ENVOY_SVC%%/*}" \
  port-forward "service/${ENVOY_SVC##*/}" 8080:80
```

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward service/mlops-monitoring-prometheus 9090:9090
```

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward svc/mlops-pushgateway 9091:9091
```

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward service/mlops-monitoring-grafana 3000:80
```

## Esportare i log di inferenza

Nel **terminale dei comandi**, generare traffico via gateway ed esportare i
record del giorno su SeaweedFS:

```bash
bash /opt/mlops-local-poc/prometheus/scripts/send-test-traffic.sh
bash /opt/mlops-local-poc/model-quality/scripts/export-logs.sh
```

Atteso: cinque righe `richiesta i/5: 200`, poi una riga simile a:

```text
upload: ../data/inference-YYYY-MM-DD.jsonl to s3://mlops-logs/inference/date=YYYY-MM-DD/inference-YYYY-MM-DD.jsonl
Export completato: s3://mlops-logs/inference/date=YYYY-MM-DD/inference-YYYY-MM-DD.jsonl
```

## Costruire il reference dataset

Creare lo snapshot di reference per la versione risolta dall'alias `champion`
(verifica lo SHA-256 del CSV, applica lo stesso preprocessing del training e
ricalcola le predizioni con la versione registrata):

```bash
bash /opt/mlops-local-poc/model-quality/scripts/build-reference.sh
```

Atteso, in coda all'output:

```text
Dataset verificato: input.csv sha256 ...
Feature dal run: 14
Predizioni ricalcolate: 8760
Reference scritta in /output: versione 1
Reference caricato: s3://mlops-logs/reference/model=v1/
```

Il lock `reference.lock.json` associa il reference a modello, versione, run e
dataset usati.

## Calcolare il drift

Il calcolo gira nell'immagine dedicata. Costruirla una volta:

```bash
cd /opt/mlops-local-poc/model-quality && ./scripts/build-image.sh
```

Scaricare il batch della data indicata e calcolare il PSI di feature e output
(formato data `YYYY-MM-DD`, default oggi):

```bash
bash /opt/mlops-local-poc/model-quality/scripts/compute-drift.sh 2026-10-12
```

Vengono scritti:

```text
/tmp/drift-metrics-2026-10-12.json
```

Controllare il risultato:

```bash
cat /tmp/drift-metrics-2026-10-12.json
```

Il file contiene `sample_count`, `features.<feature>`, `output_psi` e
`max_feature_psi`. Con un campione di test molto piccolo e ripetitivo, i
valori PSI possono essere elevati senza indicare un drift reale.

## Inviare le metriche a Prometheus

Inviare il batch al Pushgateway (etichettato con la data come instance):

```bash
bash /opt/mlops-local-poc/model-quality/scripts/push-drift-metrics.sh 2026-10-12
```

Atteso:

```text
Metriche pushate su http://127.0.0.1:9091: job=model_quality_drift, instance=2026-10-12
```

Verificare la metrica aggregata in Prometheus:

```bash
curl -sS 'http://127.0.0.1:9090/api/v1/query?query=model_quality_max_feature_psi' \
  | python3 -m json.tool
```

Atteso: `status: success` con almeno una serie con label del modello servito.

## Schedulare il batch con CronJob

Il `CronJob` esegue ogni notte lo stesso batch (legge i log via API, scarica
il reference da S3, calcola e pusha) senza passare da WSL. Richiede l'immagine
del componente nel nodo e un Secret con le credenziali S3 (l'IP di SeaweedFS
cambia a ogni ricreazione dello stack: ricreare il Secret in quel caso):

```bash
kind load docker-image gp-mof-capstone-drift:dev --name mlops-test
bash /opt/mlops-local-poc/model-quality/scripts/apply-s3-secret.sh
kubectl --context kind-mlops-test apply -f /opt/mlops-local-poc/model-quality/manifests/
```

Verificare gli oggetti:

```bash
kubectl --context kind-mlops-test -n monitoring get cronjob model-quality-drift
```

Eseguire subito un batch manuale dal `CronJob`, senza attendere la schedule:

```bash
kubectl --context kind-mlops-test -n monitoring \
  create job --from=cronjob/model-quality-drift drift-manual-02
kubectl --context kind-mlops-test -n monitoring wait --for=condition=complete \
  job/drift-manual-02 --timeout=300s
```

Atteso: `condition met`. Il batch usa come instance il giorno precedente
(UTC); controllare la nuova serie in Prometheus come nel capitolo precedente.

## Vedere la dashboard

Applicare la dashboard provisionata:

```bash
bash /opt/mlops-local-poc/model-quality/scripts/apply-dashboard.sh
```

Aprire in Grafana la dashboard **Model Quality - Drift**:

```text
http://127.0.0.1:3000/d/model-quality-drift/model-quality-drift
```

I pannelli attesi sono quattro: PSI per feature, prediction drift, PSI
massimo e campioni nel batch (le query esatte sono nel file JSON versionato;
nel browser si leggono da ciascun pannello con `Inspect > Query`).

## Esercizio facoltativo: traffico perturbato

Per provare la pipeline con un drift artificiale, inviare richieste con feature
spostate:

```bash
python3 /opt/mlops-local-poc/prometheus/scripts/send-perturbed-traffic.py \
  --request /opt/mlops-local-poc/serving/runtime/infer-request.json \
  --count 200 --shift 'Temperature(°C)=10' --shift Hour=12
```

Poi esportare, ricalcolare e pushare con instance distinta da quella
giornaliera:

```bash
bash /opt/mlops-local-poc/model-quality/scripts/export-logs.sh
bash /opt/mlops-local-poc/model-quality/scripts/compute-drift.sh 2026-10-12
bash /opt/mlops-local-poc/model-quality/scripts/push-drift-metrics.sh 2026-10-12 2026-10-12-drift-demo
```

Caveat: con batch di poche decine di righe identiche contro un reference di
8760 righe varie, il PSI satura (~10) sia nel baseline che nel batch
perturbato — il confronto non mostra un contrasto visivo. Un contrasto reale
richiederebbe un baseline di centinaia di richieste varie. Per rendersene
conto, confrontare le due instance:

```bash
curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=model_quality_output_psi' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(r['metric'].get('instance'), '=', r['value'][1]) for r in d['data']['result']]"
```

Se i valori delle due instance sono dello stesso ordine di grandezza, il batch
perturbato non emerge dal baseline.

Terminati i controlli, interrompere i port-forward con `Ctrl+C`.

## Limiti

- Il monitoring è non supervisionato: non valuta la qualità rispetto a ground
  truth.
- L'export è giornaliero e sovrascrive il file della stessa data; non è ancora
  idempotente per esecuzioni multiple.
- Il reference è associato esplicitamente alla V1 tramite `reference.lock.json`.

## Sequenza operativa ordinaria

Dopo una modifica ai file nel repository Windows:

```bash
bash "${REPO}/model-quality/scripts/sync-to-wsl.sh"
bash /opt/mlops-local-poc/model-quality/scripts/apply-dashboard.sh
```

Dopo una modifica alle dipendenze di calcolo:

```bash
cd /opt/mlops-local-poc/model-quality && ./scripts/build-image.sh
kind load docker-image gp-mof-capstone-drift:dev --name mlops-test
```

Per ricalcolare il drift su un batch (dopo nuovo export):

```bash
bash /opt/mlops-local-poc/model-quality/scripts/compute-drift.sh <YYYY-MM-DD>
bash /opt/mlops-local-poc/model-quality/scripts/push-drift-metrics.sh <YYYY-MM-DD>
```

## Stato finale della fase (punto raggiunto)

Al termine della procedura risultano verificati:

- log di inferenza esportati in `s3://mlops-logs/inference/date=.../`;
- reference dataset V1 con lock di provenance su
  `s3://mlops-logs/reference/model=v1/`;
- metriche `model_quality_*` (PSI per feature, PSI output, massimo, campioni)
  interrogabili in Prometheus;
- `CronJob` giornaliero con batch manuale di prova completato e nuova instance
  in Prometheus;
- dashboard **Model Quality - Drift** provisionata in Grafana, con pannelli
  popolati.
