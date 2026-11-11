# Progettazione CI/CD MLOps locale

Contratti ed evidenze per il [piano di implementazione](implementation-plan.md).
Le decisioni non equivalgono a funzionalità già implementate. La procedura
operativa in costruzione è [github-actions-self-hosted-wsl.md](github-actions-self-hosted-wsl.md).

## Architettura e perimetro

- Simulare un ambiente MLOps on-prem gestito usando WSL, Docker e KinD,
  disponibili durante le sessioni di laboratorio.
- GitHub Actions con runner self-hosted WSL e deploy diretto tramite kubectl.
  GitHub sarà un remote aggiuntivo del repository esistente; branch, PR,
  trigger nativi ed environment saranno gestiti su GitHub. Bitbucket è fuori
  dal flusso: niente mirror, bridge o token Bitbucket.
- Due job: train-and-evaluate e deploy. Trigger: PR aperta/aggiornata, push
  su main e avvio manuale. PR senza registrazione nel Registry o deployment.
- Checkout Linux della revisione per codice e build, non copia dei sorgenti
  dal repository Windows. Configurazioni locali, dati runtime e segreti
  restano nel perimetro operativo WSL, fuori dal checkout e dal versionamento
  dei segreti. La collocazione comune di codice e tools nel repository non
  implica che debbano essere sincronizzati/eseguiti allo stesso modo.
- Nessun registry immagini iniziale: Docker locale per training/resolver,
  kind load docker-image per MLServer.
- Environment GitHub production con approval manuale prima del deploy.
  Verificare in fase di configurazione che le protezioni richieste siano
  disponibili per piano GitHub e visibilità del repository scelti.
- Conservare alias MLflow baseline/candidate/champion; niente tag Azure
  staging/production. Baseline è storico, non fallback automatico.
- Champion viene aggiornato solo dopo readiness e smoke test riusciti.
  Rollback manuale possibile tramite selezione della versione e riapplicazione
  del manifest; niente rollback automatico.
- Fuori scope: GitOps Flux/Argo CD, registry locale/remoto, rollout graduale,
  retraining da drift/nuovi dati e modifiche non necessarie a monitoring/alerting.
- Privacy del repository e filtri sugli autori/eventi riducono l'esposizione,
  ma non isolano il runner con accesso a Docker e rete locale. Eseguire solo
  codice PR fidato, limitare GITHUB_TOKEN e non usare pull_request_target per
  eseguire il codice della PR.

## PR fidate e permessi

- Ammettere al job PR solo eventi `pull_request` con repository sorgente
  uguale a quello di destinazione e autore della PR `GiuseppePiscopo`.
  Escludere PR da fork e bot. Applicare il filtro al job prima
  dell'assegnazione al runner, non come step dopo il checkout.
- Non usare `pull_request_target` per eseguire codice PR e non aggiungere
  percorsi indiretti che eseguano codice di PR escluse.
- L'autore della PR non certifica ogni commit: eseguire solo branch con
  codice, workflow e dipendenze fidati. Questi filtri non isolano la macchina
  e non proteggono da modifiche malevole dei workflow.
- Mantenere il repository privato. Prima di un eventuale passaggio a pubblico,
  riesaminare accesso al runner, trigger PR e protezioni disponibili; il
  filtro sull'autore non rende sicura da solo la pubblicazione.
- Dichiarare `permissions: contents: read` nei workflow e
  `persist-credentials: false` nel checkout. Non concedere scrittura sul
  repository o approvazione di PR ai job; aggiungere altri permessi soltanto
  in presenza di una necessità verificata.

## Configurazione e accessi del runner

- Il workflow deve impostare esplicitamente i valori non segreti, senza
  dipendere dagli export del terminale che avvia il runner. Non servono
  Actions repository variables aggiuntive per la configurazione attuale.
- Dataset runtime esterno al checkout, montato in sola lettura:
  `TRAINING_DATA_HOST_PATH=/opt/mlops-local-poc/training/data/input/SeoulBikeData.csv`.
- Rete dei container: `DOCKER_NETWORK=mlops-local-network` e
  `DEPLOYMENT_RESOLVER_NETWORK=mlops-local-network`.
- Tracking dai container: `MLFLOW_TRACKING_URI=http://mlflow:5000`;
  esperimento `MLFLOW_EXPERIMENT_NAME=gp-mof-capstone-training` e modello
  `REGISTERED_MODEL_NAME=gp-mof-capstone-model`.
- Cluster `mlops-test`, contesto esplicito `kind-mlops-test`, kubeconfig
  dell'utente runner in `$HOME/.kube/config`.
- Conservare la configurazione non segreta del resolver in
  [deployment-target.local.yaml](../deployment/config/deployment-target.local.yaml)
  e dello storage serving in
  [storage-secret.env](../serving/config/storage-secret.env), senza duplicare
  nomi delle risorse e prefissi degli artifact in nuove configurazioni.
- `GIT_REPOSITORY` deve identificare `GITHUB_WORKSPACE`, checkout Linux del
  job. Tag e identità delle immagini derivano dal commit del job, non da
  variabili persistenti del runner.
- Credenziali S3 nel file locale `/opt/mlops-local-poc/mlflow/.env`;
  kubeconfig e file credenziali restano fuori dal checkout, con permessi
  `600`. Il training accede agli artifact tramite il proxy MLflow senza
  ricevere credenziali S3; lo script serving aggiorna il Secret Kubernetes.
- Non servono nuovi Actions secrets per questi accessi. Usare il
  `GITHUB_TOKEN` fornito da Actions per il repository, senza PAT persistente
  nel checkout. Non pubblicare credenziali, kubeconfig o contenuti dei Secret
  nei log o negli artifact.

## Stato iniziale

Ricognizione dei sorgenti e delle procedure, non verifica del cluster attuale:

- Training containerizzato, Tracking/Registry MLflow e serving KServe sono
  già completati secondo la [roadmap](../roadmap.md).
- [training_utils.py](../training/src/common/training_utils.py) prepara i dati:
  rimozione Date, get_dummies sulle categoriche, split 80/20 con seed 42.
  Eval_model calcola e registra mae/rmse/r2/mape; MAPE esclude target zero.
- [train.py](../training/src/random_forest/train.py) non applica soglie o
  confronto champion. Passa sempre registered_model_name a log_model:
  attualmente registra incondizionatamente una Model Version.
- [Build training](../training/scripts/build-image.sh) e
  [build serving](../serving/scripts/build-image.sh) accettano IMAGE_TAG,
  con default :dev e --pull. Il [build resolver](../deployment/scripts/build-image.sh)
  accetta DEPLOYMENT_RESOLVER_IMAGE, con default :dev.
- Il [launcher training](../training/scripts/run_training.py) ha default :dev
  e legge solo RepoDigests; restituisce unknown se l'elenco è vuoto.
- Il [resolver](../deployment/src/resolve_and_render.py) seleziona per alias,
  verifica READY, genera manifest e lock, ma non applica risorse.
- Il [ServingRuntime](../serving/manifests/20-serving-runtime.yaml) usa
  gp-mof-capstone-mlserver:dev con IfNotPresent.
- La [procedura serving](../serving/serving-kserve-model.md) costruisce e
  carica l'immagine nel cluster mlops-test. Le procedure attuali sincronizzano
  dal repo Windows a /opt/mlops-local-poc, senza .git nella copia operativa.
- Il nuovo percorso CI/CD e i tag per commit non sono ancora implementati.

## Training, evaluation e registrazione

Tre operazioni nello stesso job train-and-evaluate, non tre job aggiuntivi:

Preparazione, training ed evaluation sono step separati del workflow.
La preparazione acquisisce i riferimenti e i dati prima del training;
lo script di training non risolve champion e non applica il gate.
L'evaluation dedicata usa la stessa immagine training con entrypoint distinto.

1. Training/logging: salvare parametri, provenance, metriche, firma e modello
   nel run MLflow. Nel percorso CI/CD log_model non riceve
   registered_model_name: artifact/logged model non è una Model Version.
2. Evaluation: candidato e champion sul medesimo test set. Gate ordinario:
   rmse_candidate <= rmse_champion, parità ammessa, nessuna tolleranza di
   regressione o soglia assoluta aggiuntiva. MAE, R2 e MAPE informative.
   Metriche mancanti/non finite, feature incompatibili o modello non
   caricabile causano errore esplicito. Un training FINISHED non implica
   gate superato; il gate fallito termina il job con esito non-zero.
3. Registrazione esplicita: soltanto dopo gate superato, su main o avvio
   manuale, registrare il modello effettivamente valutato senza riaddestrarlo.
   Assegnare candidate alla nuova versione READY.

| Evento / gate | Run e artifact | Model Version | Candidate | Deploy |
|---|---|---|---|---|
| PR, superato | Sì | No | Invariato | No |
| PR, fallito | Se prodotti | No | Invariato | No |
| Main/manuale, fallito | Se prodotti | No | Invariato | No |
| Main/manuale, superato | Sì | Sì | Nuova versione pronta | Dopo approval |

Conservare run e artifact rifiutati per diagnosi. Non modificare champion o
baseline durante training/gate/registrazione. Le operazioni non sono atomiche:
se registrazione riesce e assegnazione alias fallisce, segnalare versione
creata ed errore e bloccare il deploy. Nessuna cancellazione silenziosa o
falsa condizione di successo.

Aggiornare launcher, CLI e procedura manuale durante l'implementazione:
il percorso non orchestrato deve restare utilizzabile.

## Identificazione e passaggio tra job

- Training produce JSON con run_id, model_uri effettivamente restituito dal
  logging e git_commit del checkout. Non cercare l'ultimo run, non presumere
  la struttura degli artifact (MLflow usa model ID), non estrarre URI dai log.
  Il launcher deve rendere il risultato disponibile al workflow.
- Report gate riferito agli stessi run_id/model_uri/commit e al champion
  concreto confrontato. Report mancante, incoerente o di un altro run blocca
  la registrazione.
- La risposta di registrazione aggiunge registered_model e model_version,
  verificata READY. Passare gli output tramite GITHUB_OUTPUT e needs al
  secondo job; conservare JSON e report come artifact della stessa esecuzione,
  identificati anche da GitHub run ID/run attempt. PR senza output versione.
- Dopo approval usare quella versione esatta, verificando stato e provenance:
  mai risolvere nuovamente candidate, che potrebbe essere cambiato. Nessun
  fallback a latest/candidate/champion se il riferimento non è recuperabile.
- Estendere resolver/CLI per versione esplicita alternativa all'alias,
  mantenendo l'uso manuale per alias. Lock con distinzione della selezione,
  versione, run sorgente e URI S3 concreti. Smoke test e promozione champion
  devono riferirsi alla versione distribuita.
- Restano da definire serializzazione dei deploy e trattamento dei run
  approvati fuori ordine: passare una versione esatta non impedisce da solo a
  un vecchio run di sovrascrivere un rilascio più recente.
- Gli output GitHub identificano il passaggio, non sono una barriera di
  sicurezza contro codice PR non fidato sul runner.

## Confronto e prove PR

- Prima del training risolvere una sola volta champion in versione READY;
  salvare Registered Model, versione, run sorgente e URI. Caricare per
  versione, mai rileggere l'alias nel gate. Se non recuperabile, fallire.
- Fissare contenuto CSV per training/evaluation; registrare SHA-256, seed,
  quota test, preprocessing e identità delle righe test. Solo il seed non
  garantisce lo stesso test set se cambiano ordine, dati o preprocessing.
  Usare lo stesso X_test/y_test per entrambi i modelli.
- Copiare il CSV runtime in una directory dedicata all'esecuzione, fuori dal
  checkout, prima del training. Fissare SHA-256 della copia nello snapshot
  iniziale e montarla in sola lettura; i passi successivi non rileggono il
  CSV runtime originale. DVC non è richiesto per questa implementazione.
- Il training salva il test set effettivo: feature, target, indici delle
  righe, ordine e tipi. Il manifest prodotto associa checksum dei file,
  identità del test set, run e hash del CSV allo snapshot iniziale verificato.
- Il contenuto del test set resta in una directory per esecuzione sotto
  `RUNNER_TEMP`, condivisa tra training ed evaluation e montata in sola lettura
  nell'evaluation. Pulire quella directory a fine job anche in caso di errore.
  In MLflow conservare soltanto manifest, identità, snapshot e report, non i
  dati del test set. Dopo la pulizia la ripetizione richiede i dati sorgente;
  il checksum non ne garantisce la recuperabilità.
- L'evaluation riceve JSON del candidato, snapshot champion e test set;
  verifica coerenza e checksum prima di misurare entrambi i modelli.
  Non ripete split o preprocessing e non accetta nuovi hash come sostituti
  dei valori attesi. File mancanti, alterazioni o riferimenti incoerenti
  causano errore esplicito.
- Report con identità di modelli/test set, entrambi i RMSE ed esito. Lo
  snapshot non garantisce che champion sia ancora servito al momento del deploy.
- Nuovo push/workflow o riesecuzione job acquisisce un nuovo snapshot;
  niente riuso implicito del report precedente. Versione e run attempt rendono
  riconoscibile un cambio di confronto.
- Dimostrazione: prima PR migliorativa passa; seconda PR regressiva fallisce;
  push correttivo sulla stessa PR passa. Nessuna registrazione/deploy da PR.
- Per confrontare gli esiti mantenere una finestra senza deploy/modifiche
  manuali agli alias; verificare identità di riferimento, dati e split.
- Se la prima PR viene promossa prima della seconda, confrontare con il nuovo
  champion. Selezionare i casi degradato/corretto rispetto a quel riferimento,
  senza ripristinare alias per ottenere verde. Parità ammessa: la correzione
  può riprodurre champion.
- Test isolato futuro: spostare l'alias dopo snapshot non deve cambiare il
  confronto; non usare l'alias reale per una prova unitaria.

## Bootstrap ed evidenze locali

- Champion assente blocca il flusso ordinario. Input bootstrap booleano,
  default false, solo workflow_dispatch; non da PR/push e non come fallback.
- Distinguere assenza effettiva di modello/alias da problemi di rete,
  autenticazione o inconsistenza. Champion esistente rifiuta il bootstrap.
- Gate bootstrap: r2 >= 0.80 E rmse <= 300 sul test set corrente.
  Metriche mancanti/non finite o training non riuscito bloccano. Queste
  soglie non si applicano al flusso relativo ordinario.
- Report con modalità bootstrap, soglie, metriche, test set e run/modello.
  Registrazione/candidate post-gate e versione esplicita tra job come sopra.
- Approval production comunque obbligatoria. Nel deploy serializzato,
  ricontrollare prima della modifica del servizio che champion non sia
  comparso: se presente, fermare e richiedere run ordinario. Verifica
  dell'assenza non è lock; serializzare insieme bootstrap e deploy ordinari.
- Champion solo dopo successo; non creare/spostare implicitamente baseline.

Verifica del 2026-11-09: MLflow 3.14.0, letture REST di modello/versioni/run,
nessun training o modifica ad alias/servizio. Alias letti: baseline/champion
V1, candidate V2. Entrambi i run FINISHED:

| Versione | Run MLflow | R2 | RMSE | Soglie |
|---|---|---|---|---|
| V1 | 192b2b53c91d400b91a76ccfea241817 | 0.8496584036867021 | 250.27836597480078 | Superate |
| V2 | 826d90e03ac14af9b31deabb4797bd97 | 0.8590028119972198 | 242.37563390082678 | Superate |

- Dataset SHA-256 comune:
  d2f7a0f0ba95e82678ead794429a18a7f696aa3b82c9b9ec34ec47806ff0767b.
  8760 righe, 7008 training/1752 test, seed 42, test size 0.2.
- V1: 100 alberi/max depth 10; V2: 300 alberi/max depth 20.
- Commit comune 414aca10d3475f3a32a5b2220da603b064d4c8f4, git.dirty=true:
  verifica sui run esistenti, non riproduzione dal checkout pulito corrente.
- Soglie compatibili con le due configurazioni riuscite; nessun bootstrap
  reale o caso degradato provato. Test futuri isolati: uguaglianza soglie
  passa, violazione di un limite fallisce, metriche mancanti/non finite,
  champion presente, errore Registry, champion comparso durante approval.
  Non cancellare champion reale per simulare il primo rilascio.

## Tag e identità immagini

### Ruolo dei tre componenti

Ricognizione dei Dockerfile e dei consumer esistenti, senza build o prove
sull'ambiente:

| Immagine | Contenuto e consumer | Dove viene eseguita | Caricamento KinD |
|---|---|---|---|
| gp-mof-capstone-training | Sorgenti e dipendenze di training; launcher run_training.py | Docker locale, rete mlops-local-network, CSV montato read-only | No |
| gp-mof-capstone-deployment-resolver | Codice, template e configurazione del resolver; render.sh | Docker locale, rete mlops-local-network, output persistito tramite bind mount | No |
| gp-mof-capstone-mlserver | MLServer, dipendenze e LoggingMLflowRuntime; ServingRuntime | Pod predictor KServe nel cluster | Sì, con kind load |

- Il [Dockerfile training](../training/Dockerfile) copia i sorgenti, non il
  dataset. Il launcher passa rete Docker, mount dati e provenance: aggiornare
  insieme build e selezione dell'immagine nel launcher.
- Il [Dockerfile resolver](../deployment/Dockerfile) incorpora anche template
  e configurazione. Il [consumer render.sh](../deployment/scripts/render.sh)
  condivide DEPLOYMENT_RESOLVER_IMAGE con il build script: usare lo stesso
  riferimento esplicito in entrambi, non lasciare il consumer su :dev.
- Il [Dockerfile serving](../serving/Dockerfile) non incorpora il modello
  addestrato: MLServer parte da /mnt/models, popolato dal percorso di storage
  KServe. Il modello resta negli artifact SeaweedFS ed è selezionato tramite
  storageUri nel manifest InferenceService.
- Una nuova Model Version non richiede di per sé una nuova immagine MLServer:
  immagine runtime e artifact modello hanno cicli di vita distinti, ma
  dipendenze/runtime devono restare compatibili con il modello selezionato.
  Nessun caricamento delle immagini training/resolver nei nodi è necessario
  per il flusso concordato.
- Per ciascun componente usare il commit dei suoi sorgenti di build e
  propagare il riferimento effettivo al relativo consumer. Un'immagine
  MLServer già verificata può essere riusata per più modelli; non presumere
  che il suo commit debba coincidere con quello di ogni nuovo training.

### Contratto comune dei tag

- Formato: nome-immagine:git-SHA_COMPLETO del commit effettivamente costruito.
  Nomi: gp-mof-capstone-training, gp-mof-capstone-deployment-resolver,
  gp-mof-capstone-mlserver. Per PR il checkout può essere un merge commit,
  non necessariamente lo SHA del branch sorgente.
- Tag di rilascio solo per checkout pulito e build context coerente. Nella
  copia /opt senza .git indicare il repository sorgente con GIT_REPOSITORY.
  Costruire dai sorgenti committati, sul filesystem Linux, non dalla copia
  sincronizzata potenzialmente obsoleta. Il dataset runtime resta esterno.
- Non sovrascrivere tag esistenti. Riuso solo con provenance verificata;
  rebuild dello stesso commit con basi/dipendenze diverse richiede riferimento
  distinto esplicito. Tag SHA non garantisce riproducibilità (build --pull).
- Acquisire sempre docker image inspect .Id (sha256:...) come image ID locale,
  riferimento completo e commit. RepoDigests identifica manifest di registry,
  può essere vuoto per build locali ed è opzionale: non serve un push.
- Distinguere image ID da RepoDigest nella provenance del launcher/run MLflow.
  Inspect fallito o image ID mancante è errore, non unknown silenzioso.
- Build MLServer, kind load e ServingRuntime usano lo stesso tag. Verificare
  identità nel nodo e immagine effettiva predictor, senza presumere uguali
  testualmente gli identificatori di Docker e containerd.
- Registrare identità negli output di build/provenance e, per training, MLflow.

## Riferimento Azure ML

Lab locale di esempio: C:\Dati\WS\BC\PoC\mlops-fundamentals-poc.

- [Istruzioni fase 3](../../mlops-fundamentals-poc/docs/phase3-lab-instructions.md):
  setup GitHub, approval, prove PR/regressione/correzione e rollback manuale.
- [Workflow](../../mlops-fundamentals-poc/.github/workflows/ml-pipeline.yml):
  due job, versione esatta tra job, registrazione esclusa nelle PR, smoke test
  prima della promozione e cancellazione dei run PR superati.
- [Evaluation](../../mlops-fundamentals-poc/tools/evaluate.py): run FINISHED,
  soglie assolute R2 >= 0.80/RMSE <= 300, exit non-zero e riepilogo tabellare.

Il workflow Azure attuale già esclude registrazione PR. Metriche Azure
R2/RMSE, locali r2/rmse. Soglie Azure riusate solo per bootstrap, verificate
localmente come sopra. Non importare OIDC Azure, endpoint gestiti o tag Azure.

## Accesso operativo e port-forward

Prima di operare verificare Git, contesto Kubernetes, versioni/alias e servizio.
Non ricreare il cluster o rimuovere risorse per eseguire prove one-shot.

I servizi KinD richiedono port-forward secondo le rispettive procedure.
Al 2026-11-09 l'utente segnala che non sono attivi; verificare nuovamente a
ogni sessione. MLflow è stato raggiunto dalla porta del suo stack Docker:
questo non prova accessibilità dei servizi KinD.

Individuare i forward necessari (possono essere più di uno), verificare se
esistono e usare terminali dedicati o processi in background gestiti perché
restano in foreground. Verificare readiness/raggiungibilità; arrestare solo
quelli avviati per la prova. Non duplicare forward o arrestare processi
dell'utente; concordare esplicitamente se devono restare disponibili oltre
la verifica.
