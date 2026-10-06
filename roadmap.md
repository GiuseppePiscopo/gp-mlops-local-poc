Ecco la roadmap ad alto livello definita nella sessione precedente per l'intero lab MLOps locale:

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

## Prossimo sviluppo

**Monitoring** — osservabilità del modello servito, in due lab distinti, da dettagliare quando sarà il caso:

1. **Infrastruttura di monitoring** — stack che raccoglie e visualizza le metriche nel cluster (oggi assente), verificato sul predictor già servito
2. **Monitoring del modello servito** — collegamento dello stack al serving. Aree da valutare:
   - metriche del servizio (richieste, latenza, errori) e stato del predictor
   - logging delle richieste e delle predizioni
   - monitoraggio della qualità: drift dei dati e delle predizioni rispetto al training

## Estensioni successive

- **CI/CD** — automazione di build delle immagini, training, registrazione, rendering e applicazione del manifest
- **Promozione e rollback** — cambio controllato della versione servita, rilascio graduale e ritorno alla versione precedente (rollback automatico)
- **Riaddestramento** — trigger da monitoring o da nuovi dati, con nuova versione nel Model Registry
- **Esposizione e sicurezza** — accesso al servizio senza port-forward manuale, autenticazione, gestione dei segreti
