# Roadmap MLOps Locale

Ecco la roadmap ad alto livello per l'intero lab MLOps locale:

## Già completato

**Fase 1 — Infrastruttura di base**
- KServe in modalità Standard (cert-manager, Gateway API, Envoy Gateway) con un `InferenceService` Iris precompilato servito tramite port-forward
- Stack MLflow Tracking Server + PostgreSQL (backend store) + SeaweedFS (artifact store S3-compatible)

**Fase 2 — Training containerizzato + MLflow** *(completata)*
All'interno di questa fase, gli 8 passaggi concettuali della "vertical slice" erano:

1. Training (container Docker che esegue il training)
2. Tracking (parametri, metriche, tag su MLflow)
3. Artifact storage (modello depositato su SeaweedFS)
4. Model Registry (versioni registrate)
5. Confronto versioni (V1 vs V2 sulle metriche)
6. Selezione/promozione (alias `baseline`/`candidate`/`champion`)

**Deployment — risoluzione MLflow e rendering del manifest KServe** *(completato)*
- Risoluzione dell'alias MLflow, verifica dello stato della versione, traduzione dell'URI degli artefatti in URI S3 e generazione del manifest `InferenceService` con lock JSON di provenance.

**Serving KServe** *(completato)*
- Modello registrato servito tramite `InferenceService` con `modelFormat: mlflow` e protocollo V2, su un `ServingRuntime` namespaced dedicato (immagine MLServer con dipendenze allineate al modello), accesso agli artefatti S3 tramite Secret e ServiceAccount.
- Verifica dell'inferenza diretta sul predictor e tramite Envoy Gateway (port-forward). Il test Iris resta indipendente.

**Monitoring — infrastruttura** *(completato)*
- Stack Prometheus + Grafana + kube-state-metrics via `kube-prometheus-stack` ([procedura](prometheus/monitoring-stack-setup.md)).
- Dashboard del predictor: disponibilità, restart, CPU e memoria.
- Metriche MLServer raccolte via `ServiceMonitor` (porta `metrics` dichiarata nel `ServingRuntime`).
- Metriche Envoy/Gateway raccolte (control-plane via `ServiceMonitor`, proxy via `PodMonitor`) e verificate con traffico di prova via gateway.

**Monitoring del servizio di inferenza** *(completato)*
- Dashboard di performance ed errori in directory dedicata `inference-monitoring/` ([procedura](inference-monitoring/service-monitoring-setup.md)): richieste al secondo per stato HTTP, tasso di errore, richieste in corso, latenza media/p95.
- Split pre/post-processing: MLServer espone solo durata dell'`infer` e dell'handling HTTP; la differenza (~3%) è mostrata come serie `overhead server`. Instrumentation custom rimandata (decisione registrata nella procedura).
- Code assenti per architettura (MLServer monoreplica senza batching): concorrenza coperta dal pannello richieste in corso; risorse CPU/memoria nella dashboard del predictor.

**Request/response logging** *(completato)*
- Runtime MLServer custom (`LoggingMLflowRuntime`) che registra ogni inferenza in JSONL su stdout: feature, predizioni, timestamp, versione e request id ([procedura](inference-logging/request-response-logging.md)).
- Campionamento via `LOG_SAMPLE_RATE`, retention affidata alla rotation di kubelet; formato pronto per la conversione in Parquet nel lab di qualità.

**Monitoring della qualità del modello** *(completato)*
- Export giornaliero dei log su SeaweedFS, reference dataset V1 versionato con lock, calcolo PSI su feature e predizioni ([procedura](model-quality/model-quality-setup.md)).
- Batch schedulato con `CronJob` giornaliero, metriche `model_quality_*` via Pushgateway in Prometheus e dashboard Grafana del drift.

## Prossimi sviluppi

Nessuno al momento.

## Estensioni successive

- **CI/CD** — automazione di build delle immagini, training, registrazione, rendering e applicazione del manifest
- **Promozione e rollback** — cambio controllato della versione servita, rilascio graduale e ritorno alla versione precedente (rollback automatico)
- **Riaddestramento** — trigger da monitoring o da nuovi dati, con nuova versione nel Model Registry
- **Alerting** — soglie sulle metriche di drift e qualità, notifiche e trigger automatici
- **Esposizione e sicurezza** — accesso al servizio senza port-forward manuale, autenticazione, gestione dei segreti
