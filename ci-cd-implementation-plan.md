# Piano di implementazione CI/CD MLOps locale

> Documento di lavoro temporaneo. Serve da punto di ripresa per le prossime
> sessioni. Al completamento della CI/CD, dopo aver trasferito la procedura
> stabile nella documentazione del lab e aggiornato la roadmap, questo piano
> potrà essere rimosso.

## Obiettivo

Automatizzare il percorso locale già realizzato: training, valutazione,
registrazione/promozione tramite alias MLflow, rendering del manifest KServe,
deploy su KinD e verifica dell'inferenza. Il workflow deve simulare un
ambiente MLOps on-prem gestito, eseguito durante il laboratorio su WSL.

Il documento operativo definitivo, analogo alle procedure di training,
deployment resolution e serving, sarà creato durante l'implementazione e
conterrà prerequisiti, configurazione, comandi, uso del workflow, verifiche e
diagnostica.

## Decisioni confermate

- **Orchestrazione:** GitHub Actions con runner self-hosted in WSL; deployment
  diretto al cluster tramite `kubectl`. Niente Flux/Argo CD nella prima
  implementazione.
- **Immagini:** niente registry nella prima iterazione. Le immagini di training
  e del resolver sono usate dal Docker Engine locale; l'immagine MLServer viene
  caricata nel nodo KinD con `kind load docker-image`.
- **Tag immagini:** sostituire il tag mobile `dev` con tag riferiti al commit
  Git (e registrare, dove disponibile, il digest). Verificare che il runtime
  KServe utilizzi esattamente il tag caricato nel cluster. Il tag SHA è
  immutabile per convenzione; il digest è il riferimento più forte.
- **Sorgenti e dati:** il codice oggetto del workflow viene preso dal checkout
  della revisione Git nel workspace Linux del runner. Configurazioni locali,
  dati runtime e segreti restano nel perimetro operativo WSL esistente e non
  vengono spostati nel checkout né committati.
- **Repository CI/CD:** GitHub sarà aggiunto come remote del repository
  esistente. Branch e PR usati per il lab CI/CD saranno pubblicati e gestiti su
  GitHub, così Actions riceve direttamente gli eventi nativi; Bitbucket non
  farà parte del flusso CI/CD e non serviranno mirror, bridge o token Bitbucket.
- **Trigger:** PR aperta/aggiornata, push su `main` e avvio manuale.
- **PR:** eseguono training ed evaluation, ma non creano model version nel
  Registry e non fanno deploy.
- **Push su `main` e run manuali:** eseguono training ed evaluation; se
  superate, registrano il modello e impostano l'alias `candidate`.
- **Approvazione:** il job di deployment usa un GitHub Actions environment
  `production` protetto da approvazione manuale.
- **Promozione:** il deploy risolve `candidate` in una versione concreta e
  conserva manifest e lock di provenance. Solo dopo deploy e smoke test
  riusciti aggiorna `champion` alla versione servita. Si mantengono gli alias
  locali `baseline`, `candidate` e `champion`; non si introducono i tag Azure
  ML `staging`/`production`.
- **Rollback:** fuori scope. In caso di necessità resta possibile il rollback
  manuale selezionando una versione/alias e riapplicando il relativo manifest.

## Stato iniziale verificato

- [x] Roadmap: training containerizzato e MLflow Registry sono già presenti;
  la registrazione corrente avviene dal codice di training.
- [x] `training/scripts/build-image.sh` supporta `IMAGE_TAG`; il launcher
  `training/scripts/run_training.py` ha un'immagine predefinita `:dev`.
- [x] `deployment/scripts/build-image.sh` supporta
  `DEPLOYMENT_RESOLVER_IMAGE`; il resolver genera manifest e lock, ma non li
  applica.
- [x] `serving/scripts/build-image.sh` supporta `IMAGE_TAG`; il
  `ServingRuntime` in `serving/manifests/20-serving-runtime.yaml` è invece
  fissato a `gp-mof-capstone-mlserver:dev`.
- [x] `serving/serving-kserve-model.md` costruisce l'immagine MLServer e la
  carica nel nodo `mlops-test` con `kind load docker-image`.
- [x] Le procedure correnti sincronizzano componenti dal repository Windows
  verso `/opt/mlops-local-poc`; il workflow userà il checkout per il codice,
  evitando di trattare la copia Windows come sorgente dei sorgenti eseguiti.
- [x] Working tree pulito al momento della creazione di questo piano.

## Piano di attività

Le caselle non spuntate indicano lavoro ancora da fare. Aggiornare questo
documento durante le sessioni successive, includendo i risultati delle prove e
le decisioni che cambiano.

### 1. Definire i contratti di training ed evaluation

- [ ] Ispezionare e documentare le metriche e la logica di valutazione esistenti
  (`training/src/common/training_utils.py` e `training/src/random_forest/train.py`).
- [ ] Definire il confronto con `champion`/`baseline`: metrica primaria,
  direzione di miglioramento, tolleranze e comportamento se l'alias non esiste.
  Non introdurre soglie arbitrarie: concordarle sulla base delle metriche del
  lab.
- [ ] Separare chiaramente la produzione di metriche/artifact dalla
  registrazione nel Model Registry: PR non registra versioni; `main` e
  workflow manuale registrano solo dopo il superamento dell'evaluation.
- [ ] Stabilire come il job successivo identifica senza ambiguità il run e la
  versione registrata, senza affidarsi a una risoluzione tardiva di alias
  mutabili.
- [ ] Verificare che la gestione del primo rilascio, senza `champion` esistente,
  sia definita e testabile.
- [ ] Definire come fissare e aggiornare il riferimento di confronto durante
  le prove PR, così un cambiamento concorrente dell'alias `champion` non altera
  l'esito atteso del test.

### 2. Preparare e provare i tag immutabili per le immagini

- [ ] Definire un formato di tag comune basato sul commit, preferibilmente lo
  SHA completo; registrare il digest Docker prodotto.
- [ ] Valutare i tre componenti immagine separatamente: training, deployment
  resolver e serving MLServer. Evitare di richiedere a KinD immagini che usa
  solo il runner.
- [ ] Rendere configurabile il riferimento dell'immagine nel manifest
  `ServingRuntime` senza compromettere l'uso manuale documentato.
- [ ] Aggiornare la procedura di serving con build, `kind load`, verifica nel
  nodo e gestione dei tag SHA. Conservare una modalità operativa one-shot
  semplice per provare l'immagine prima di automatizzare il workflow.
- [ ] Eseguire la prova nell'ambiente WSL/Docker/KinD esistente: buildare una
  nuova immagine, caricarla, applicare il `ServingRuntime` aggiornato e
  verificare che il predictor avvii proprio quell'immagine.
- [ ] Evitare modifiche distruttive o ricreazioni del cluster; tornare al
  riferimento precedente solo con una modifica esplicita e verificata.

### 3. Preparare l'ambiente runner e il perimetro di sicurezza

- [ ] Configurare il remote GitHub aggiuntivo e verificare che push, branch e
  PR del lab siano effettuati sul repository GitHub corretto. Mantenere
  esplicito il remote predefinito per evitare push accidentali al remote
  Bitbucket.
- [ ] Installare/configurare il runner self-hosted in WSL e verificare accesso
  a Docker, `kind`, `kubectl`, MLflow, SeaweedFS e dataset runtime.
- [ ] Configurare il runner per il repository e un'etichetta dedicata; non
  esporlo a workflow/repository diversi da quelli previsti.
- [ ] Definire i prerequisiti persistenti in WSL (`/opt/mlops-local-poc`,
  stack MLflow, dataset, contesto KinD, credenziali) e i segreti/variabili
  necessari al workflow. Non stampare o versionare segreti.
- [ ] Usare il checkout della revisione come sorgente dei Docker build e del
  codice eseguito. Passare al training il commit Git del checkout, non i
  metadati di una copia operativa separata.
- [ ] Per le PR, limitare l'uso del runner al codice considerato fidato. La
  privacy del repository e filtri sugli eventi/autori riducono l'esposizione,
  ma non isolano il runner. Non usare `pull_request_target` per eseguire il
  codice della PR.
- [ ] Limitare i permessi di `GITHUB_TOKEN` e non rendere disponibili job di
  deployment o credenziali privilegiate alle PR.

### 4. Implementare workflow e job

- [ ] Aggiungere workflow GitHub Actions per PR, push su `main` e
  `workflow_dispatch`, con condizioni esplicite: le PR non raggiungono il
  deployment.
- [ ] Implementare il job training/evaluation usando checkout del codice,
  build immagine con tag commit, dataset runtime e accesso a MLflow.
- [ ] Salvare gli output necessari (metriche, run ID, eventuale report) e
  renderli consultabili nell'esecuzione GitHub Actions.
- [ ] Dopo evaluation superata, registrare il modello soltanto per push su
  `main` o avvio manuale e assegnare `candidate`; in caso di fallimento
  interrompere il flusso prima del deployment.
- [ ] Implementare il job deploy dipendente dal job precedente e protetto
  dall'environment `production`.
- [ ] Nel job deploy, risolvere la versione candidata in modo deterministico,
  generare manifest e lock, caricare in KinD il runtime MLServer con il tag
  atteso, applicare le risorse necessarie e il manifest `InferenceService`.
- [ ] Attendere la readiness e fare smoke test d'inferenza. Aggiornare
  `champion` solo al successo; un errore non deve spostare l'alias.
- [ ] Conservare come output del workflow almeno versione/run MLflow, commit,
  immagine/digest, manifest e lock di provenance, senza includere credenziali.

### 5. Verificare i percorsi e i casi di errore

- [ ] PR: workflow eseguito, evaluation visibile, nessuna model version
  registrata, nessuna approvazione/deploy.
- [ ] Eseguire una dimostrazione end-to-end delle PR, analoga al lab Azure ML:
  una prima PR che migliora il modello e passa il confronto; una seconda PR
  che introduce intenzionalmente una regressione e viene rifiutata
  dall'evaluation; un nuovo push correttivo sulla stessa PR che supera
  nuovamente i controlli. Verificare nei tre casi l'esito del workflow e che
  non ci siano registrazioni nel Registry o deploy dalle PR.
- [ ] Push su `main`: registrazione e assegnazione `candidate` solo a
  evaluation superata; deploy sospeso in attesa dell'approvazione
  `production`.
- [ ] Run manuale: comportamento equivalente a `main`, con parametri espliciti
  e validati se si decide di esporli.
- [ ] Rifiuto/mancata approvazione: nessun deploy e nessun aggiornamento di
  `champion`.
- [ ] Evaluation fallita, render fallito, immagini non disponibili, predictor
  non pronto o smoke test fallito: errore esplicito, niente falsa condizione di
  successo e alias `champion` invariato.
- [ ] Deployment riuscito: il manifest/lock identifica la versione effettiva,
  l'inferenza funziona e `champion` punta a quella versione.
- [ ] Verificare il rollback manuale documentando la procedura, senza
  automatizzarlo.

### 6. Documentare e chiudere

- [ ] Creare il documento operativo definitivo, ad esempio
  `ci-cd/github-actions-self-hosted-wsl.md`, analogo alle procedure esistenti.
  Includere setup runner, environment e protezioni GitHub, configurazione,
  trigger, flusso dei job, bootstrap WSL, esecuzione, immagini, verifiche,
  troubleshooting, sicurezza e rollback manuale.
- [ ] Aggiornare `roadmap.md` segnando CI/CD come completata e registrando
  sinteticamente le scelte operative effettive.
- [ ] Validare il documento e la roadmap rispetto al workflow finale e alle
  prove eseguite.
- [ ] Rimuovere questo piano temporaneo solo quando il documento operativo è
  completo, il workflow è verificato e lo stato è aggiornato in roadmap.

## Fuori scope per questa iterazione

- GitOps con Flux o Argo CD.
- Registry remoto o locale per le immagini.
- Rollback automatico, rollout graduale o promozione progressiva.
- Riaddestramento automatico in risposta a drift/nuovi dati.
- Modifiche al monitoraggio/alerting non necessarie per verificare il flusso.

## Passaggio di consegne

Alla ripresa del lavoro:

1. leggere questo piano e verificare le caselle completate rispetto ai file e
   allo stato dell'ambiente;
2. controllare lo stato Git e le modifiche locali prima di intervenire;
3. riprendere dalla prima attività non completata, aggiornando il piano con
   prove, esiti e decisioni;
4. non eseguire operazioni sul cluster o sul Registry senza verificare prima
   contesto Kubernetes, alias/versioni MLflow e stato attuale del servizio.
