# Monitoring del servizio di inferenza

Questa procedura crea la dashboard di performance ed errori del servizio di
inferenza `gp-mof-capstone-model`, usando solo metriche già esposte da
MLServer: richieste, tasso di errore, latenza e richieste in corso.

La latenza distinta per fase di pre/post-processing e predizione non è
esposta da MLServer: l'unico split ottenibile senza codice è l'overhead
server, incluso come serie nel pannello di latenza (si veda la nota dedicata).
L'instrumentation custom resta rimandata.

## Prerequisiti

- WSL2 con Docker Desktop integrato;
- `kind`, `kubectl`, `curl` e `python3` disponibili in WSL;
- accesso `sudo` e directory `/opt/mlops-local-poc/` posseduta dall'utente WSL;
- cluster `mlops-test`, contesto `kind-mlops-test`;
- [procedura di serving](../serving/serving-kserve-model.md) completata, con
  file di richiesta in `/opt/mlops-local-poc/serving/runtime/infer-request.json`;
- [stack di monitoring](../prometheus/monitoring-stack-setup.md) completato:
  Prometheus, Grafana e `ServiceMonitor` del predictor attivi;
- repository completo della directory `inference-monitoring/`.

Tutti i comandi si eseguono in un terminale **Bash in WSL**, salvo dove è
indicato un secondo terminale dedicato ai port-forward.

## Preparare la directory operativa e sincronizzare

Impostare il percorso del repository Windows visto da WSL:

```bash
export REPO="/mnt/c/<percorso-repository>/mlops-local-poc"
ls -d "${REPO}/inference-monitoring"

sudo install -d -o "$USER" -g "$(id -gn)" -m 0755 \
  /opt/mlops-local-poc/inference-monitoring

bash "${REPO}/inference-monitoring/scripts/sync-to-wsl.sh"
```

Il comando `ls -d` deve stampare il percorso, senza errori.

```bash
find /opt/mlops-local-poc/inference-monitoring -maxdepth 2 -type f | sort
```

Devono essere presenti almeno:

```text
/opt/mlops-local-poc/inference-monitoring/dashboards/service-performance-dashboard.json
/opt/mlops-local-poc/inference-monitoring/scripts/apply-dashboard.sh
/opt/mlops-local-poc/inference-monitoring/scripts/sync-to-wsl.sh
```

## Verificare le metriche disponibili

MLServer espone i contatori di esito, un riepilogo delle durate di inferenza
e le metriche del server REST (concorrenza, durate per path e stato HTTP).
Verificare che Prometheus li raccolga:

```bash
curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=count by (__name__)({job="gp-mof-capstone-model-predictor", __name__=~"model_.+|rest_.+"})' \
  | python3 -m json.tool
```

Atteso: `status: success` con le famiglie `model_infer_request_success_total`,
`model_infer_request_failure_total`, `model_infer_request_duration_count`,
`model_infer_request_duration_sum`, `rest_server_requests_total`,
`rest_server_requests_in_progress` e
`rest_server_request_duration_seconds_count` (ciascuna accompagnata dalle
serie `_created` generate dal client Prometheus).

Se il port-forward verso Prometheus non è attivo, avviarlo prima (capitolo
seguente) e ripetere.

## Avviare i port-forward

Nel **terminale dei port-forward** (WSL), lasciare attivi:

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward service/mlops-monitoring-prometheus 9090:9090
```

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward service/mlops-monitoring-grafana 3000:80
```

Per generare traffico via gateway serve anche quello verso Envoy (il nome del
Service include un suffisso variabile: ricavarlo via label):

```bash
ENVOY_SVC=$(kubectl --context kind-mlops-test get svc -A \
  -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway \
  -o jsonpath='{.items[0].metadata.namespace}/{.items[0].metadata.name}')

kubectl --context kind-mlops-test -n "${ENVOY_SVC%%/*}" \
  port-forward "service/${ENVOY_SVC##*/}" 8080:80
```

## Generare traffico di prova

Nel **terminale dei comandi**, inviare richieste tramite il gateway con lo
script già versionato nel componente `prometheus/`:

```bash
bash /opt/mlops-local-poc/prometheus/scripts/send-test-traffic.sh
```

Atteso: cinque righe `richiesta i/5: 200`.

## Creare la dashboard di performance ed errori

Il file `dashboards/service-performance-dashboard.json` definisce la
dashboard **Servizio inferenza gp-mof-capstone-model** con quattro pannelli:
richieste al secondo, tasso di errore, richieste in corso e latenza (media,
p95, overhead server).
Lo script `scripts/apply-dashboard.sh` lo pubblica come ConfigMap
etichettata, raccolta automaticamente dal sidecar delle dashboard di Grafana.
Applicarlo:

```bash
bash /opt/mlops-local-poc/inference-monitoring/scripts/apply-dashboard.sh
```

Verificare la ConfigMap:

```bash
kubectl --context kind-mlops-test -n monitoring \
  get configmap gp-mof-capstone-service-dashboard --show-labels
```

Atteso: una riga con `DATA` uguale a `1` e label `grafana_dashboard=1`.

Verificare che Grafana l'abbia caricata:

```bash
curl -s -u admin:admin 'http://127.0.0.1:3000/api/search?query=inferenza' \
  | python3 -m json.tool
```

Atteso: un elemento con `title` uguale a
`Servizio inferenza gp-mof-capstone-model` e `uid` uguale a
`gp-mof-capstone-service`.

Aprire la dashboard nel browser:

```text
http://localhost:3000/d/gp-mof-capstone-service/servizio-inferenza-gp-mof-capstone-model
```

I pannelli attesi sono quattro: richieste al secondo distinte per stato HTTP,
tasso di errore, richieste in corso e latenza media con p95 (le query esatte
sono nel file JSON versionato; nel browser si leggono da ciascun pannello con
`Inspect > Query`).

## Verificare i pannelli con dati freschi

Subito dopo il traffico di prova, tutti i pannelli mostrano dati: rate delle
`200` intorno a `5 richieste / 5 minuti`, tasso di errore `0` (se tutte le
richieste hanno avuto esito `200`), richieste in corso `0` a traffico
terminato, curve di latenza media e p95 popolate.

Se i pannelli di rate risultano piatti, il traffico è più vecchio della
finestra di `5m`: ripetere il capitolo di generazione traffico e ricontrollare
dopo `15s`.

## Nota sullo split pre/post-processing

MLServer espone la durata dell'intero `infer` del runtime
(`model_infer_request_duration_*`) e quella dell'intero handling HTTP
(`rest_server_request_duration_seconds_*`), ma nessuna metrica per singola
fase (decodifica, predict, codifica). L'unico split ottenibile senza codice è
la differenza tra le due medie, mostrata come serie `overhead server` nel
pannello di latenza: nel lab vale circa `2ms`, il `3%` del totale.

Si registra qui la decisione: instrumentation custom del runtime (sottoclasse
di `MLflowRuntime` con Histogram per fase, rebuild immagine) sproporzionata
rispetto al valore informativo. Rivalutare se un modello futuro avrà fasi di
pre/post-processing pesanti.

Terminati i controlli, interrompere i port-forward con `Ctrl+C`.

## Sequenza operativa ordinaria

Dopo una modifica ai file nel repository Windows:

```bash
bash "${REPO}/inference-monitoring/scripts/sync-to-wsl.sh"

bash /opt/mlops-local-poc/inference-monitoring/scripts/apply-dashboard.sh
```

## Stato finale della fase (punto raggiunto)

Al termine della procedura risultano verificati:

- directory operativa `/opt/mlops-local-poc/inference-monitoring/` sincronizzata;
- famiglie di metriche di esito, durata e concorrenza raccolte da Prometheus;
- traffico di prova via gateway con esito `200`;
- dashboard **Servizio inferenza gp-mof-capstone-model** provisionata in
  Grafana, con pannelli di richieste, errore, concorrenza e latenza (media,
  p95, overhead server) popolati;
- decisione sullo split pre/post-processing registrata (overhead ~3%,
  instrumentation custom rimandata).
