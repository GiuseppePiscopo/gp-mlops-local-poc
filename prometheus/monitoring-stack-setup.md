# Setup dello stack di monitoring (Prometheus + Grafana)

Questa procedura installa lo stack minimo di monitoring per il cluster MLOps
locale, collega a Prometheus le metriche MLServer del predictor servito e
quelle di Envoy/Gateway, e crea la dashboard Grafana del predictor, fino alla
verifica dei target di scrape, dei pannelli e delle metriche raccolte.

## Prerequisiti

- WSL2 con Docker Desktop integrato e Docker Compose funzionante;
- `helm`, `kind`, `kubectl`, `curl` e `python3` disponibili in WSL;
- accesso `sudo` e directory `/opt/mlops-local-poc/` posseduta dall'utente WSL;
- cluster `mlops-test`, contesto `kind-mlops-test`, KServe in modalità Standard
  già installato ([procedura KServe](../kserve/kserve-kind-wsl-setup.md));
- [procedura di serving](../serving/serving-kserve-model.md) completata con i
  file del repository corrente: `InferenceService` `gp-mof-capstone-model` nel
  namespace `gp-mof-capstone-serving`, predictor `Running`, richiesta di
  inferenza V2 già verificata e Service predictor con porta `metrics`
  (`8082 -> 8082`);
- repository completo della directory `prometheus/`.

Tutti i comandi si eseguono in un terminale **Bash in WSL**, salvo dove è
indicato un secondo terminale dedicato ai port-forward.

## Preparare la directory operativa e sincronizzare

Impostare il percorso del repository Windows visto da WSL:

```bash
export REPO="/mnt/c/<percorso-repository>/mlops-local-poc"
ls -d "${REPO}/prometheus"

sudo install -d -o "$USER" -g "$(id -gn)" -m 0755 \
  /opt/mlops-local-poc/prometheus

bash "${REPO}/prometheus/scripts/sync-to-wsl.sh"
```

Il comando `ls -d` deve stampare il percorso, senza errori.

```bash
find /opt/mlops-local-poc/prometheus -maxdepth 2 -type f | sort
```

Devono essere presenti almeno:

```text
/opt/mlops-local-poc/prometheus/config/values-kube-prometheus-stack.yaml
/opt/mlops-local-poc/prometheus/config/service-monitor-mlserver.yaml
/opt/mlops-local-poc/prometheus/config/service-monitor-envoy-gateway.yaml
/opt/mlops-local-poc/prometheus/config/pod-monitor-envoy-proxy.yaml
/opt/mlops-local-poc/prometheus/dashboards/predictor-dashboard.json
/opt/mlops-local-poc/prometheus/scripts/apply-dashboard.sh
/opt/mlops-local-poc/prometheus/scripts/send-test-traffic.sh
/opt/mlops-local-poc/prometheus/scripts/sync-to-wsl.sh
```

## Installare lo stack di monitoring

Aggiungere il repository Helm e creare il namespace:

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts
helm repo update

kubectl --context kind-mlops-test create namespace monitoring \
  --dry-run=client -o yaml | kubectl --context kind-mlops-test apply -f -
```

Installare `kube-prometheus-stack` con i valori del repository (retention `7d`,
scrape ed evaluation interval `15s`, Alertmanager disabilitato, scrape job dei
componenti assenti in KinD disabilitati):

```bash
helm --kube-context kind-mlops-test upgrade --install mlops-monitoring \
  prometheus-community/kube-prometheus-stack --version 92.3.0 \
  -n monitoring \
  -f /opt/mlops-local-poc/prometheus/config/values-kube-prometheus-stack.yaml
```

I valori includono `serviceMonitorSelectorNilUsesHelmValues: false` e
`serviceMonitorNamespaceSelector: {}`, così Prometheus raccoglie anche il
`ServiceMonitor` nel namespace di serving.

Attendere i pod:

```bash
kubectl --context kind-mlops-test -n monitoring get pods -o wide
```

Atteso: `Running` per Grafana, kube-state-metrics, operator, node-exporter e
`prometheus-mlops-monitoring-prometheus-0` (`2/2`).

## Verificare Prometheus

Nel **terminale dei port-forward** (WSL), lasciare attivo:

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward service/mlops-monitoring-prometheus 9090:9090
```

Nel **terminale dei comandi**, verificare i target attivi:

```bash
curl -s http://127.0.0.1:9090/api/v1/targets \
  | python3 -c "import json,sys; d=json.load(sys.stdin); at=d['data']['activeTargets']; print('active:', len(at)); [print(t['discoveredLabels'].get('__address__'), t['labels'].get('job'), t['health']) for t in at]"
```

Atteso: tutti i target con `health` uguale a `up`, tra cui:

```text
apiserver
coredns
kubelet
mlops-monitoring-grafana
kube-state-metrics
mlops-monitoring-operator
node-exporter
mlops-monitoring-prometheus
```

La stessa vista è disponibile nel browser su `http://localhost:9090/targets`.

## Verificare Grafana

Nel **terminale dei port-forward**, aggiungere:

```bash
kubectl --context kind-mlops-test -n monitoring \
  port-forward service/mlops-monitoring-grafana 3000:80
```

Aprire `http://localhost:3000`. Al primo accesso usare utente `admin` e password
`admin`, quindi impostare una nuova password. Agli accessi successivi usare
la password impostata.

Il datasource Prometheus è già provisionato: verificarlo in
`Connections > Data sources > Prometheus` con `Save & test`.

Atteso: esito positivo del test del datasource.

## Collegare le metriche MLServer

La procedura di serving lascia il Service del predictor con la porta `metrics`
(`8082 -> 8082`). Verificare:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get svc gp-mof-capstone-model-predictor \
  -o jsonpath='{range .spec.ports[*]}{.name} {.port}->{.targetPort}{"\n"}{end}'
```

Atteso:

```text
http 80->8080
metrics 8082->8082
```

Il file `config/service-monitor-mlserver.yaml` seleziona quel Service e ne
raccoglie `/metrics` ogni `15s` sulla porta nominata. Controllare l'endpoint:

```bash
grep -E 'port:|path:|interval:' \
  /opt/mlops-local-poc/prometheus/config/service-monitor-mlserver.yaml
```

Atteso:

```text
- port: metrics
path: /metrics
interval: 15s
```

Applicarlo:

```bash
kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/prometheus/config/service-monitor-mlserver.yaml

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get servicemonitor gp-mof-capstone-model-mlserver
```

Il secondo `get` deve mostrare la risorsa creata, senza errori.

Attendere uno scrape interval (`15s`) e riverificare i target:

```bash
curl -s http://127.0.0.1:9090/api/v1/targets \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(t['discoveredLabels'].get('__address__'), t['labels'].get('job'), t['health']) for t in d['data']['activeTargets'] if 'capstone' in json.dumps(t).lower()]"
```

Atteso: una riga simile a:

```text
10.244.0.x:8082 gp-mof-capstone-model-predictor up
```

(l'IP del pod varia a ogni rollout).

## Creare la dashboard del predictor

Il file `dashboards/predictor-dashboard.json` definisce la dashboard
**Predictor gp-mof-capstone-model** con quattro pannelli: disponibilità,
restart, CPU e memoria. Lo script `scripts/apply-dashboard.sh` lo pubblica
come ConfigMap etichettata, raccolta automaticamente dal sidecar delle
dashboard di Grafana. Applicarlo:

```bash
bash /opt/mlops-local-poc/prometheus/scripts/apply-dashboard.sh
```

Verificare la ConfigMap:

```bash
kubectl --context kind-mlops-test -n monitoring \
  get configmap gp-mof-capstone-predictor-dashboard --show-labels
```

Atteso: una riga con `DATA` uguale a `1` e label `grafana_dashboard=1`.

Verificare che Grafana l'abbia caricata, inserendo la password impostata quando
richiesta:

```bash
curl -s -u admin 'http://127.0.0.1:3000/api/search?query=predictor' \
  | python3 -m json.tool
```

Atteso: un elemento con `title` uguale a `Predictor gp-mof-capstone-model` e
`uid` uguale a `gp-mof-capstone-predictor`.

Aprire la dashboard nel browser:

```text
http://localhost:3000/d/gp-mof-capstone-predictor/predictor-gp-mof-capstone-model
```

I pannelli attesi sono quattro: disponibilità, restart, CPU e memoria
(le query esatte sono nel file JSON versionato; nel browser si leggono da
ciascun pannello con `Inspect > Query`). Verificare che tutti mostrino dati:
disponibilità uguale a `1`, restart `0` se il pod non è mai ripartito, curve
di CPU e memoria popolate.

## Verificare la metrica di inferenza

La metrica `model_infer_request_success_total` compare dopo almeno
un'inferenza riuscita. Se la [procedura di serving](../serving/serving-kserve-model.md)
è stata completata, è già disponibile:

```bash
curl -s 'http://127.0.0.1:9090/api/v1/query?query=model_infer_request_success_total' \
  | python3 -m json.tool
```

Atteso: `status: success` con almeno una serie del tipo:

```json
{
  "model": "gp-mof-capstone-model",
  "namespace": "gp-mof-capstone-serving",
  "job": "gp-mof-capstone-model-predictor"
}
```

Se il risultato è vuoto, eseguire un'inferenza V2 secondo la procedura di
serving e ripetere la query dopo `15s`.

## Integrare le metriche di Envoy/Gateway

Il gateway espone metriche su due piani, entrambi già verificati come
raggiungibili dalla rete del cluster:

- control-plane `envoy-gateway`: `/metrics` sulla porta del Service nominata
  `metrics` (`19001 -> 19001`);
- proxy Envoy del gateway `kserve-ingress-gateway`: `/stats/prometheus` sulla
  porta container `19001`, con una serie per route servita (tra cui quelle del
  modello). Il Service del proxy espone solo la porta `80`, quindi qui serve
  un `PodMonitor` che punta al pod invece del Service.

Verificare i prerequisiti di discovery:

```bash
kubectl --context kind-mlops-test -n envoy-gateway-system \
  get svc envoy-gateway \
  -o jsonpath='{range .spec.ports[*]}{.name} {.port}->{.targetPort}{"\n"}{end}' | grep metrics

kubectl --context kind-mlops-test -n envoy-gateway-system \
  get pods -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway \
  -o jsonpath='{range .items[*].spec.containers[*].ports[*]}{.containerPort}/{.name} {end}{"\n"}'
```

Atteso: `metrics 19001->19001` per il Service e `19001/metrics` tra le porte
del container `envoy` del proxy.

Il file `config/service-monitor-envoy-gateway.yaml` seleziona il Service del
control-plane. Controllare l'endpoint e applicarlo:

```bash
grep -E 'port:|path:|interval:' \
  /opt/mlops-local-poc/prometheus/config/service-monitor-envoy-gateway.yaml

kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/prometheus/config/service-monitor-envoy-gateway.yaml
```

Il file `config/pod-monitor-envoy-proxy.yaml` seleziona i pod del proxy dalla
label del gateway che li possiede. Controllare l'endpoint e applicarlo:

```bash
grep -E 'port(Number)?:|path:|interval:' \
  /opt/mlops-local-poc/prometheus/config/pod-monitor-envoy-proxy.yaml

kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/prometheus/config/pod-monitor-envoy-proxy.yaml
```

Attendere un paio di minuti (la discovery dei pod è più lenta di quella dei
Service) e riverificare i target:

```bash
curl -s http://127.0.0.1:9090/api/v1/targets \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(t['discoveredLabels'].get('__address__'), t['labels'].get('job'), t['health']) for t in d['data']['activeTargets'] if 'envoy' in json.dumps(t).lower() or 'gateway' in json.dumps(t).lower()]"
```

Atteso: due righe con `health` uguale a `up`, del tipo:

```text
10.244.0.x:19001 envoy-gateway up
10.244.0.y:19001 monitoring/envoy-proxy up
```

(gli IP dei pod variano a ogni rollout).

Verificare le metriche raccolte, tra cui i contatori per route del modello
(ancora a `0` se non è transitato traffico dal gateway):

```bash
curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=envoy_cluster_upstream_rq_total' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], 'series:', len(d['data']['result'])); [print(' ', r['metric'].get('envoy_cluster_name'), '=', r['value'][1]) for r in d['data']['result'] if 'capstone' in r['metric'].get('envoy_cluster_name', '')]"
```

Atteso: `status: success` con le serie delle route di
`gp-mof-capstone-serving`.

## Generare traffico di prova

Cinque inferenze via gateway bastano a muovere i contatori di entrambi i
piani. Nel **terminale dei port-forward**, avviare quello verso Envoy (il nome
del Service include un suffisso variabile: ricavarlo via label):

```bash
ENVOY_SVC=$(kubectl --context kind-mlops-test get svc -A \
  -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway \
  -o jsonpath='{.items[0].metadata.namespace}/{.items[0].metadata.name}')

kubectl --context kind-mlops-test -n "${ENVOY_SVC%%/*}" \
  port-forward "service/${ENVOY_SVC##*/}" 8080:80
```

Nel **terminale dei comandi**, generare il traffico con lo script del
repository (di default 5 richieste; richiede il file di richiesta prodotto
dalla procedura di serving):

```bash
bash /opt/mlops-local-poc/prometheus/scripts/send-test-traffic.sh
```

Atteso: cinque righe `richiesta i/5: 200`.

Riverificare i contatori dopo `15s`:

```bash
curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=envoy_cluster_upstream_rq_total{envoy_cluster_name=~".*capstone.*"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(r['metric'].get('envoy_cluster_name'), '=', r['value'][1]) for r in d['data']['result']]"
```

Atteso: la serie della route `.../gp-mof-capstone-model/rule/0` aumentata
dello stesso numero di richieste inviate, mentre quella di
`.../gp-mof-capstone-model-predictor/rule/0` (chiamata diretta) resta ferma.
Anche `model_infer_request_success_total` cresce dello stesso numero.

La stessa verifica si fa dal browser: in Grafana aprire **Explore** (icona
bussola nel menu di sinistra), selezionare il datasource **Prometheus**,
passare in modalità **Code**, digitare la query
`envoy_cluster_upstream_rq_total` ed eseguire con **Run query**. La vista
**Table** mostra il valore corrente per route, quella **Time series** il
grafico.

Terminati i controlli, interrompere i port-forward con `Ctrl+C`.

## Sequenza operativa ordinaria

Dopo una modifica ai file nel repository Windows:

```bash
bash "${REPO}/prometheus/scripts/sync-to-wsl.sh"

helm --kube-context kind-mlops-test upgrade --install mlops-monitoring \
  prometheus-community/kube-prometheus-stack --version 92.3.0 \
  -n monitoring \
  -f /opt/mlops-local-poc/prometheus/config/values-kube-prometheus-stack.yaml

kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/prometheus/config/service-monitor-mlserver.yaml

kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/prometheus/config/service-monitor-envoy-gateway.yaml

kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/prometheus/config/pod-monitor-envoy-proxy.yaml

bash /opt/mlops-local-poc/prometheus/scripts/apply-dashboard.sh
```

## Stato finale della fase (punto raggiunto)

Al termine della procedura risultano verificati:

- namespace `monitoring` e release Helm `mlops-monitoring` (chart
  `kube-prometheus-stack-92.3.0`) attivi, con tutti i pod `Running`;
- target Prometheus predefiniti (`apiserver`, `coredns`, `kubelet`, Grafana,
  kube-state-metrics, operator, node-exporter, Prometheus) tutti `up`;
- Grafana raggiungibile su `http://localhost:3000`, datasource Prometheus
  testato con esito positivo;
- `ServingRuntime` con porte `8080/http` e `8082/metrics` dichiarate, Service
  predictor con porta `metrics 8082 -> 8082`;
- `ServiceMonitor` applicato e target `gp-mof-capstone-model-predictor`
  su `:8082` con `health: up`;
- dashboard **Predictor gp-mof-capstone-model** provisionata in Grafana, con
  pannelli di disponibilità, restart, CPU e memoria popolati;
- metrica `model_infer_request_success_total` del modello servito
  interrogabile in Prometheus;
- target `envoy-gateway` (control-plane) e `monitoring/envoy-proxy`
  (data-plane) con `health: up`, con le serie `envoy_cluster_upstream_rq_total`
  per le route del modello;
- traffico di prova via gateway con esito `200` e contatori Envoy e MLServer
  corrispondentemente incrementati.

Con questo il lab **Infrastruttura di monitoring** è completo. Il passo
successivo della roadmap è il **Monitoring del servizio di inferenza**
(richieste, errori, latenze, dashboard di performance).
