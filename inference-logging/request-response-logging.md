# Request/response logging delle inferenze

Questa procedura aggiunge al runtime MLServer la registrazione strutturata di
ogni inferenza (feature di input e predizioni in JSONL su stdout), fino alla
verifica dei record nei log del predictor.

## Prerequisiti

- WSL2 con Docker Desktop integrato e Docker Compose funzionante;
- `kind`, `kubectl`, `curl` e `python3` disponibili in WSL;
- accesso `sudo` e directory `/opt/mlops-local-poc/` posseduta dall'utente WSL;
- cluster `mlops-test`, contesto `kind-mlops-test`;
- [procedura di serving](../serving/serving-kserve-model.md) completata, con
  file di richiesta in `/opt/mlops-local-poc/serving/runtime/infer-request.json`;
- [stack di monitoring](../prometheus/monitoring-stack-setup.md) completato,
  con script di traffico in `/opt/mlops-local-poc/prometheus/`;
- repository completo delle directory `serving/` e `inference-logging/`.

Tutti i comandi si eseguono in un terminale **Bash in WSL**, salvo dove è
indicato un secondo terminale dedicato ai port-forward.

## Preparare le directory operative e sincronizzare

Il runtime custom vive in `serving/src/` (incluso nel build context
dell'immagine); gli script di verifica in `inference-logging/scripts/`.

```bash
export REPO="/mnt/c/<percorso-repository>/mlops-local-poc"
ls -d "${REPO}/serving" "${REPO}/inference-logging"

sudo install -d -o "$USER" -g "$(id -gn)" -m 0755 \
  /opt/mlops-local-poc/serving \
  /opt/mlops-local-poc/inference-logging

bash "${REPO}/serving/scripts/sync-to-wsl.sh"
bash "${REPO}/inference-logging/scripts/sync-to-wsl.sh"
```

```bash
find /opt/mlops-local-poc/serving/src /opt/mlops-local-poc/inference-logging -type f | sort
```

Devono essere presenti almeno:

```text
/opt/mlops-local-poc/serving/src/logging_runtime.py
/opt/mlops-local-poc/inference-logging/scripts/verify-log-records.py
/opt/mlops-local-poc/inference-logging/scripts/verify-logs.sh
```

## Ricostruire l'immagine e avviare il rollout

```bash
cd /opt/mlops-local-poc/serving && ./scripts/build-image.sh

kind load docker-image gp-mof-capstone-mlserver:dev --name mlops-test
```

Applicare il `ServingRuntime`, che dichiara il runtime custom e le variabili
di logging (`MODEL_NAME`, `MODEL_VERSION`, `LOG_SAMPLE_RATE`):

```bash
kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/serving/manifests/20-serving-runtime.yaml

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  rollout status deployment/gp-mof-capstone-model-predictor --timeout=180s
```

Atteso: `successfully rolled out`. Il rollout è guidato da KServe a partire
dal `ServingRuntime`: non usare `rollout restart`, le modifiche manuali al
Deployment vengono annullate. Per aggiornamenti di sola immagine, cancellare
il pod e lasciare che il ReplicaSet lo ricrei:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  delete pod -l app=isvc.gp-mof-capstone-model-predictor

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  rollout status deployment/gp-mof-capstone-model-predictor --timeout=180s
```

## Ripristinare l'accesso allo storage

Verificare lo stato del pod:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get pods -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase'
```

Atteso: `READY` uguale a `true` e `STATUS` uguale a `Running`. In caso
contrario, ispezionare l'init-container del pod non pronto:

```bash
POD=$(kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get pods --field-selector=status.phase!=Running \
  -o jsonpath='{.items[0].metadata.name}')

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  logs "$POD" -c storage-initializer --tail=3
```

Se compare `Could not connect to the endpoint URL`, l'IP di SeaweedFS è
cambiato dopo un riavvio Docker. Riapplicare il Secret (risolve l'IP
corrente) e ricreare il pod:

```bash
/opt/mlops-local-poc/serving/scripts/apply-storage-secret.sh

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  delete pod -l app=isvc.gp-mof-capstone-model-predictor

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  rollout status deployment/gp-mof-capstone-model-predictor --timeout=180s
```

## Generare traffico e verificare i record

Nel **terminale dei port-forward**, avviare quello verso Envoy (il nome del
Service include un suffisso variabile: ricavarlo via label):

```bash
ENVOY_SVC=$(kubectl --context kind-mlops-test get svc -A \
  -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway \
  -o jsonpath='{.items[0].metadata.namespace}/{.items[0].metadata.name}')

kubectl --context kind-mlops-test -n "${ENVOY_SVC%%/*}" \
  port-forward "service/${ENVOY_SVC##*/}" 8080:80
```

Nel **terminale dei comandi**, generare il traffico e verificare:

```bash
bash /opt/mlops-local-poc/prometheus/scripts/send-test-traffic.sh
bash /opt/mlops-local-poc/inference-logging/scripts/verify-logs.sh
```

Atteso: cinque righe `richiesta i/5: 200`, poi un riepilogo simile a:

```text
righe JSON: 5
record validi: 5
ultimo: gp-mof-capstone-model v1 input: 14 output: 1
schema OK
```

Ogni record contiene `timestamp`, `model`, `version`, `request_id`, `inputs` e
`outputs`. Il logging non interrompe mai l'inferenza: in caso di errore di
serializzazione la risposta viene comunque restituita.

## Vedere i log

I record sono radi tra i log del server: leggere una finestra ampia e
filtrare le righe JSON.

Ultimi record grezzi:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  logs deployment/gp-mof-capstone-model-predictor -c kserve-container \
  --since=24h --tail=2000 | grep '^{'
```

Seguire il traffico dal vivo:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  logs deployment/gp-mof-capstone-model-predictor -c kserve-container \
  -f | grep '^{'
```

Ultimo record formattato:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  logs deployment/gp-mof-capstone-model-predictor -c kserve-container \
  --since=24h --tail=2000 | grep '^{' | tail -n 1 | python3 -m json.tool | head -n 30
```

Terminati i controlli, interrompere i port-forward con `Ctrl+C`.

## Campionamento e retention

- Campionamento con la variabile `LOG_SAMPLE_RATE` nel `ServingRuntime`
  (`1.0` nel lab: registra tutto).
- Versione del modello con la variabile `MODEL_VERSION`: aggiornarla quando
  cambia la versione servita.
- Retention affidata alla rotation dei log di kubelet; export verso storage
  dedicato al lab di monitoring della qualità.

## Sequenza operativa ordinaria

Dopo una modifica ai file nel repository Windows:

```bash
bash "${REPO}/serving/scripts/sync-to-wsl.sh"
bash "${REPO}/inference-logging/scripts/sync-to-wsl.sh"

cd /opt/mlops-local-poc/serving && ./scripts/build-image.sh

kind load docker-image gp-mof-capstone-mlserver:dev --name mlops-test

kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/serving/manifests/20-serving-runtime.yaml
```

Dopo il rollout, generare traffico e riverificare con `send-test-traffic.sh`
e `verify-logs.sh`.

## Stato finale della fase (punto raggiunto)

Al termine della procedura risultano verificati:

- immagine `gp-mof-capstone-mlserver:dev` con runtime custom ricostruita e
  caricata nel nodo KinD;
- predictor `1/1` con implementazione `logging_runtime.LoggingMLflowRuntime`;
- traffico di prova via gateway con esito `200`;
- record JSONL validi nei log del predictor, con feature, predizioni,
  timestamp, versione e request id.
