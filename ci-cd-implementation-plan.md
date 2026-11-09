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
- **Gate ordinario:** confronto relativo con `champion`, senza soglie assolute:
  il candidato passa se il suo RMSE è minore o uguale a quello di `champion`.
  Entrambi sono valutati sul medesimo test set del run corrente. R2, MAE e
  MAPE restano informative; `baseline` resta il riferimento storico, non un
  fallback automatico.
- **Bootstrap:** se `champion` non esiste, il flusso ordinario fallisce.
  Consentire il primo rilascio solo con avvio manuale e opzione esplicita di
  bootstrap, soglie assolute e approvazione del deploy. Soglie:
  R2 >= 0.80 e RMSE <= 300, verificate sulle metriche dei run locali V1/V2.
  I test dell'implementazione del gate restano da eseguire. Errori di
  accesso al Registry o di caricamento del modello non sono casi di bootstrap.
- **Push su `main` e run manuali:** eseguono training ed evaluation; se
  superate, registrano il modello e impostano l'alias `candidate`.
- **Approvazione:** il job di deployment usa un GitHub Actions environment
  `production` protetto da approvazione manuale.
- **Promozione:** il deploy usa la versione concreta restituita dalla
  registrazione del proprio run, senza risolvere nuovamente `candidate`, e
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

## Esempio di riferimento: lab Azure ML

Il laboratorio precedente è disponibile localmente in
`C:\Dati\WS\BC\PoC\mlops-fundamentals-poc`. Consultare:

- [Istruzioni fase 3](../mlops-fundamentals-poc/docs/phase3-lab-instructions.md):
  setup GitHub, approval, prove PR con regressione e correzione, rollback manuale.
- [Workflow](../mlops-fundamentals-poc/.github/workflows/ml-pipeline.yml):
  due job, output della versione esatta tra i job, registrazione esclusa nelle
  PR, smoke test prima della promozione, cancellazione dei run PR superati.
- [Evaluation](../mlops-fundamentals-poc/tools/evaluate.py):
  controllo run `FINISHED`, soglie assolute R2 >= 0.80 e RMSE <= 300,
  esito non-zero al fallimento e tabella nel riepilogo GitHub Actions.

Non copiare le soglie come requisiti del gate ordinario locale: qui è stato
scelto il confronto relativo con `champion`. Per il solo bootstrap si propone
di riusare le soglie Azure, verificate sui run locali esistenti (vedi bootstrap).
Le metriche Azure sono denominate `R2`/`RMSE`, quelle locali
`r2`/`rmse`. Il workflow Azure attuale già esclude la registrazione nelle PR,
coerentemente con il comportamento richiesto per questo lab. Autenticazione
Azure/OIDC, endpoint gestiti e tag Azure non appartengono alla soluzione locale.

## Piano di attività

Le caselle non spuntate indicano lavoro ancora da fare. Aggiornare questo
documento durante le sessioni successive, includendo i risultati delle prove e
le decisioni che cambiano.

### 1. Definire i contratti di training ed evaluation

- [x] Ispezionare e documentare le metriche e la logica di valutazione esistenti
  (`training/src/common/training_utils.py` e `training/src/random_forest/train.py`).
- **Comportamento attuale verificato:** split train/test 80/20 con seed 42;
  il modello è valutato sul test set con MAE, RMSE, R2 e MAPE. MAPE esclude
  le righe con target zero. Le quattro metriche sono stampate e registrate nel
  run MLflow; non esiste una soglia di accettazione né un confronto col
  `champion`/`baseline`. Dopo la valutazione, `mlflow.sklearn.log_model` riceve
  sempre `registered_model_name`, quindi la registrazione nel Model Registry
  avviene incondizionatamente nel codice di training. Di conseguenza, l'attuale
  comportamento non soddisfa ancora il requisito CI/CD di valutare prima e
  registrare solo dopo il passaggio, solo per `main`/run manuali.
- [x] Definire il confronto con `champion`/`baseline`: metrica primaria,
  direzione di miglioramento, tolleranze e comportamento se l'alias non esiste.
  Regola concordata: `rmse_candidate <= rmse_champion`, senza tolleranza di
  regressione aggiuntiva e con parità ammessa. Valutare i due modelli sullo
  stesso test set del run corrente, non confrontare direttamente metriche
  storiche di run potenzialmente diversi. Metriche mancanti/non finite,
  incompatibilità di feature o modello non caricabile devono produrre errore
  esplicito, non un passaggio del gate. `champion` assente blocca il flusso
  ordinario; il bootstrap richiede un percorso manuale esplicito.
- [x] Definire la separazione tra produzione di metriche/artifact e
  registrazione nel Model Registry: PR non registra versioni; `main` e
  workflow manuale registrano solo dopo il superamento dell'evaluation.
  Contratto descritto sotto; la modifica al codice resta da implementare
  nella fase 4.
- [x] Stabilire come il job successivo identifica senza ambiguità il run e la
  versione registrata, senza affidarsi a una risoluzione tardiva di alias
  mutabili. Contratto descritto sotto; implementazione e prove restano nella
  fase 4.
- [x] Definire il primo rilascio senza `champion`, i casi di test e verificare
  sui run locali le soglie concordate (R2 >= 0.80, RMSE <= 300).
  Contratto ed evidenze sotto. Verifica delle metriche eseguita in sola
  lettura; implementazione e test isolati del bootstrap restano nella fase 4.
- [ ] Definire come fissare e aggiornare il riferimento di confronto durante
  le prove PR, così un cambiamento concorrente dell'alias `champion` non altera
  l'esito atteso del test.

#### Contratto training, evaluation e registrazione

Tre operazioni distinte nello stesso job `train-and-evaluate`, non tre job
GitHub aggiuntivi:

1. **Training e logging:** addestrare il modello e salvare nel run MLflow
   parametri, provenance, metriche, firma e artifact del modello. Nel percorso
   CI/CD, `mlflow.sklearn.log_model` non deve ricevere
   `registered_model_name`: il modello viene salvato per essere valutato, ma
   non viene creata una Model Version nel Registry. Un artifact/logged model
   MLflow non equivale a una versione del Registered Model.
2. **Evaluation:** valutare il modello appena prodotto e il `champion` sul
   medesimo test set, secondo la regola concordata. Conservare il risultato
   della valutazione per la consultazione; regressioni ed errori devono
   interrompere il job con esito non-zero. Un training `FINISHED` non implica
   che il gate sia superato. I run, le metriche e gli artifact dei modelli
   rifiutati restano disponibili per diagnosi, senza versioni nel Registry.
3. **Registrazione esplicita:** solo dopo gate superato e solo per push su
   `main` o avvio manuale, registrare esattamente il modello valutato, senza
   riaddestrarlo, e assegnare alla versione pronta l'alias `candidate`.
   Questo completa il primo job; approval e deploy appartengono al secondo.

| Evento / esito gate | Run e artifact MLflow | Nuova Model Version | Alias `candidate` | Deploy |
|---|---|---|---|---|
| PR, superato | Sì | No | Invariato | No |
| PR, fallito | Sì, se prodotti | No | Invariato | No |
| `main` o manuale, fallito | Sì, se prodotti | No | Invariato | No |
| `main` o manuale, superato | Sì | Sì | Nuova versione pronta | Solo dopo approval |

Queste sono le condizioni del flusso previsto, non una garanzia di atomicità:
se la registrazione riesce ma l'assegnazione di `candidate` fallisce, la
versione potrebbe già esistere. Segnalare esplicitamente l'errore e la versione
creata, bloccare il deploy e non nascondere la registrazione parziale.
Training, gate e registrazione non modificano mai `champion` o `baseline`.

In fase di implementazione aggiornare anche launcher, opzioni CLI e procedura manuale:
la separazione è qui definita, ma il codice attuale registra ancora durante
il training e non è stato modificato in questo step.

#### Identificazione del modello e passaggio tra i job

- **Training -> evaluation:** produrre un risultato JSON strutturato con
  `run_id`, `model_uri` del modello effettivamente restituito dal logging e
  `git_commit` del checkout. Non ricavare il modello cercando l'ultimo run e
  non dedurre l'URI da una struttura di directory presunta: MLflow salva già
  modelli con un proprio model ID. Il launcher deve rendere disponibile il
  risultato al workflow senza estrarlo dal testo dei log.
- **Evaluation -> registrazione:** il report del gate deve riferirsi agli
  stessi `run_id`, `model_uri` e commit, e identificare il `champion` concreto
  usato nel confronto. Registrare quell'URI solo se il report corrisponde agli
  input del run corrente e attesta il superamento del gate. Report mancante,
  incoerente o di un altro run blocca la registrazione.
- **Registrazione -> deploy:** aggiungere al risultato `registered_model` e
  `model_version`, ottenuti dalla risposta della registrazione e validati in
  stato `READY`. Esporre questi riferimenti come output del primo job tramite
  `GITHUB_OUTPUT` e consultarli nel secondo tramite `needs`. Conservare il
  risultato JSON e il report come artifact della stessa esecuzione GitHub,
  identificata anche da run ID e run attempt GitHub. Le PR non producono
  output di versione registrata e non avviano il job deploy.
- **Dopo approval:** il deploy legge il Registered Model e la versione esatti
  restituiti dal proprio job precedente, verifica stato e corrispondenza della
  provenance, quindi renderizza il manifest per quella versione. Se nel
  frattempo `candidate` cambia, non sostituire la versione approvata con la
  nuova destinazione dell'alias. Un riferimento rimosso o incoerente produce
  un errore, senza fallback a `latest`, `candidate` o `champion`.
- **Rendering e promozione:** estendere il resolver con una selezione esplicita
  per versione, alternativa all'alias. Mantenere il percorso manuale per
  alias e adattare il lock per distinguere selezione per alias e per versione.
  Il lock deve continuare a riportare versione, run sorgente e URI S3 concreti.
  Smoke test e aggiornamento di `champion` devono riferirsi alla medesima
  versione distribuita.

Questo contratto evita la sostituzione del candidato durante l'approval, ma
non risolve da solo l'ordine tra rilasci concorrenti: nella fase 4 definire
serializzazione dei deploy e gestione delle approvazioni di run precedenti,
per evitare che un vecchio run approvato in ritardo sovrascriva un rilascio
più recente. Gli output GitHub identificano il passaggio nel workflow, non
sono una barriera di sicurezza contro codice PR non fidato sul runner.

#### Bootstrap manuale senza champion

- Esporre un input booleano `bootstrap`, disabilitato di default, solo per
  `workflow_dispatch`. Non consentire bootstrap da PR o push, né un fallback
  automatico quando il confronto ordinario non è possibile.
- Verificare l'assenza del riferimento `champion`: distinguere l'assenza
  effettiva del Registered Model o dell'alias da errori di rete,
  autenticazione e inconsistenze. Se `champion` esiste, rifiutare la richiesta
  di bootstrap anziché aggirare il gate relativo.
- Valutare il modello sul test set del run corrente. Soglie concordate:
  entrambe le condizioni `r2 >= 0.80` e `rmse <= 300` devono essere vere;
  metriche mancanti/non finite o training non completato correttamente
  bloccano il gate. Non applicare queste soglie al flusso ordinario.
- Conservare nel report modalità bootstrap, soglie, metriche, identità del
  test set, run e modello valutato. Registrare e assegnare `candidate` solo a
  gate superato; mantenere lo stesso contratto di versione esplicita tra job.
- Richiedere comunque approval nell'environment `production`. Prima di
  modificare il servizio, nel percorso di deploy serializzato, verificare di
  nuovo che `champion` non sia comparso durante l'attesa; in tal caso fermare
  il bootstrap e richiedere un nuovo run ordinario. La sola verifica di
  assenza non è un lock: la serializzazione prevista nella fase 4 deve
  coprire i deploy ordinari e bootstrap.
- Aggiornare `champion` soltanto dopo deploy e smoke test riusciti.
  Non creare o spostare implicitamente `baseline`, che resta un riferimento
  storico gestito esplicitamente.

Validazione locale eseguita il 2026-11-09:

- MLflow 3.14.0, interrogazioni REST in sola lettura di Registered Model,
  versioni e run sorgenti; nessun training o modifica ad alias/servizio.
  Stato letto: `baseline` e `champion` su V1, `candidate` su V2.

| Versione | Run MLflow | Stato | R2 | RMSE | Soglie bootstrap |
|---|---|---|---|---|---|
| V1 | `192b2b53c91d400b91a76ccfea241817` | FINISHED | 0.8496584036867021 | 250.27836597480078 | Superate |
| V2 | `826d90e03ac14af9b31deabb4797bd97` | FINISHED | 0.8590028119972198 | 242.37563390082678 | Superate |

- Entrambi i run riportano dataset SHA-256
  `d2f7a0f0ba95e82678ead794429a18a7f696aa3b82c9b9ec34ec47806ff0767b`,
  8760 righe, 7008 training/1752 test, seed 42 e test size 0.2.
  V1 usa 100 alberi/max depth 10; V2 300 alberi/max depth 20.
- Entrambi riportano commit
  `414aca10d3475f3a32a5b2220da603b064d4c8f4` e `git.dirty=true`:
  queste evidenze validano le soglie sui run esistenti, ma non costituiscono
  una riproduzione dal checkout pulito corrente.
- Le soglie sono compatibili con le due configurazioni locali già riuscite.
  Non è stato eseguito un bootstrap reale né provato un caso degradato:
  i test del gate implementato restano necessari.

Validazione dell'implementazione ancora da eseguire:

- Durante l'implementazione verificare con test isolati: valori esattamente
  sulle soglie (pass), uno dei due limiti violato (fail), metrica mancante/non
  finita, `champion` presente, errore del Registry e comparsa di `champion`
  durante l'approval. Non cancellare il `champion` reale per simulare il
  primo rilascio.

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
- [ ] Implementare nel training e nel launcher la separazione definita nella
  fase 1: logging del modello senza registrazione implicita nel percorso
  CI/CD, gate distinto e registrazione esplicita post-gate. Aggiornare le
  opzioni CLI e la procedura manuale di training per mantenere utilizzabile
  anche il percorso non orchestrato.
- [ ] Salvare gli output necessari (metriche, run ID, eventuale report) e
  renderli consultabili nell'esecuzione GitHub Actions.
- [ ] Dopo evaluation superata, registrare il modello soltanto per push su
  `main` o avvio manuale e assegnare `candidate`; in caso di fallimento
  interrompere il flusso prima del deployment.
- [ ] Implementare i risultati JSON, il report del gate e gli output tra i job
  secondo il contratto di identificazione della fase 1; validare la
  corrispondenza tra modello valutato, versione registrata e versione servita.
- [ ] Estendere resolver, CLI, lock e documentazione per la selezione esplicita
  della versione, preservando il rendering manuale per alias. Definire
  serializzazione dei deploy e trattamento dei run approvati fuori ordine.
- [ ] Implementare il job deploy dipendente dal job precedente e protetto
  dall'environment `production`.
- [ ] Nel job deploy, usare la versione esplicita restituita dal primo job,
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
