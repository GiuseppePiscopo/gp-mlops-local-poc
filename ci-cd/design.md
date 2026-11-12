# Progettazione CI/CD MLOps locale

Decisioni e contratti della CI/CD implementata nel laboratorio.
Per le istruzioni operative e le verifiche usare
[github-actions-self-hosted-wsl.md](github-actions-self-hosted-wsl.md).

## Architettura e perimetro

- Simulare un ambiente MLOps on-prem gestito usando WSL, Docker e KinD,
  disponibili durante le sessioni di laboratorio.
- GitHub Actions con runner self-hosted WSL e deploy diretto tramite kubectl.
  GitHub è un remote aggiuntivo del repository esistente; branch, PR,
  trigger nativi ed environment sono gestiti su GitHub. Bitbucket è fuori
  dal flusso: niente mirror, bridge o token Bitbucket.
- Due job: train-and-evaluate e deploy. Trigger: PR aperta/aggiornata, push
  su main e avvio manuale. PR senza registrazione nel Registry o deployment.
- Limitare i trigger automatici alle modifiche che coinvolgono training,
  script CI/CD, serving, deployment, workflow della pipeline o `.gitattributes`.
  Escludere Markdown e cartelle di test; mantenere disponibile l'avvio
  manuale indipendentemente dai percorsi modificati. I filtri effettivi sono
  nel [workflow](../.github/workflows/ml-pipeline.yml).
- Checkout Linux della revisione per codice e build, non copia dei sorgenti
  dal repository Windows. Configurazioni locali, dati runtime e segreti
  restano nel perimetro operativo WSL, fuori dal checkout e dal versionamento
  dei segreti. La collocazione comune di codice e tools nel repository non
  implica che debbano essere sincronizzati/eseguiti allo stesso modo.
- Nessun registry immagini iniziale: Docker locale per training/resolver,
  kind load docker-image per MLServer.
- Environment GitHub production con approval manuale prima del deploy.
  Repository pubblico per rendere disponibili Required reviewers sul piano
  corrente. Self-review consentita per il laboratorio mono-utente,
  bypass amministratore disabilitato e deployment consentito soltanto dal
  branch `main`, senza tag.
  La [documentazione GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments#required-reviewers)
  limita Required reviewers ai repository pubblici sui piani Free/Pro/Team.
- Conservare alias MLflow baseline/candidate/champion; niente tag Azure
  staging/production. Baseline è storico, non fallback automatico.
- Champion viene aggiornato solo dopo readiness e smoke test riusciti.
  Rollback manuale tramite manifest e immagine di un precedente rilascio
  riuscito; riallineare champion solo dopo readiness e inferenza verificate,
  senza modificare baseline/candidate. Niente rollback automatico.
- Fuori scope: GitOps Flux/Argo CD, registry locale/remoto, rollout graduale,
  retraining da drift/nuovi dati e modifiche non necessarie a monitoring/alerting.
- Approval dei workflow esterni e filtri sugli autori/eventi riducono l'esposizione,
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
- Nel repository pubblico richiedere approval dei workflow per tutti i
  contributori esterni.
  Non approvare workflow di PR esterne: la PR puo modificare anche il filtro
  nel workflow. L'approval di `production` non protegge il job training.
- La restrizione di `production` a `main` non è una branch protection. Non presumere
  obblighi di review o controlli sui push derivanti dall'environment.
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

Il percorso manuale non orchestrato resta utilizzabile tramite le procedure
di training, deployment e serving.

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
- Selezione per versione esplicita nel deploy, mantenendo l'uso manuale per
  alias. Lock con distinzione della selezione,
  versione, run sorgente e URI S3 concreti. Smoke test e promozione champion
  devono riferirsi alla versione distribuita.
- Gli output GitHub identificano il passaggio, non sono una barriera di
  sicurezza contro codice PR non fidato sul runner.

### Verifica del servizio e promozione

- La readiness deve riferirsi al nuovo rilascio, non a uno stato del servizio
  precedente. Verificare identità del modello, storage e immagine effettivamente
  avviata rispetto al target approvato.
- Lo smoke test verifica il predictor con protocollo V2, senza dipendere da
  port-forward gestiti manualmente; il routing Envoy è fuori dal perimetro.
  Usare l'esempio salvato negli artifact del modello e richiedere una
  predizione numerica finita per ogni riga, senza fissare valori di output
  che cambiano al cambiare del modello.
- Un cambio del pod o del container durante la prova invalida la verifica.
  Prima di promuovere ripetere i controlli Registry sul target e sul champion
  dello snapshot, mantenendo la sezione serializzata. Aggiornare solo
  `champion` alla versione esatta verificata e confermare la lettura
  dell'alias; non modificare `baseline` o `candidate`.
- Errori di readiness o inferenza impediscono la promozione, senza rollback
  automatico del servizio. Un errore di scrittura o conferma dell'alias può
  lasciare champion già aggiornato: segnalare l'errore e richiedere verifica
  dello stato, senza dichiarare un ripristino.

### Serializzazione e approvazioni fuori ordine

- Il job deploy usa un gruppo GitHub Actions `concurrency` stabile per modello
  e ambiente, condiviso da deploy ordinari e Initial model only. Non includere
  commit, branch, run ID o modalita nel gruppo. Impostare
  `cancel-in-progress: false` e `queue: max`: un solo deploy in esecuzione,
  senza sostituire automaticamente l'unico job in attesa con un nuovo arrivo.
  La coda resta soggetta ai limiti GitHub e non garantisce l'ordine dei
  workflow originari o delle approval.
- La sezione serializzata comprende i controlli dopo approval, il rendering,
  il caricamento del runtime, l'applicazione delle risorse, readiness,
  inferenza e aggiornamento di champion. Non serializzare l'intero workflow
  durante training o attesa dell'approval.
- Dopo approval e prima di modificare il servizio, nel percorso ordinario
  rileggere champion e confrontarne modello, versione, run sorgente e source
  con il riferimento fissato nello snapshot di training e nel report.
  Questa lettura verifica l'attualita del confronto: non sostituisce lo
  snapshot, non ripete l'evaluation e non seleziona il modello da distribuire.
- Champion assente o diverso dal riferimento rende il rilascio obsoleto:
  fallire esplicitamente prima del deploy, riportando riferimento valutato
  e riferimento corrente. Richiedere una nuova esecuzione con snapshot,
  evaluation e approval nuovi. Errori di lettura Registry bloccano comunque
  il deploy, senza considerarli assenza o consentire fallback.
- In Initial model only verificare invece che champion sia ancora assente,
  come previsto dal contratto bootstrap. Se e comparso, fallire e richiedere
  una nuova esecuzione ordinaria.
- Esempio: A e B sono valutati contro champion V1; B viene distribuito e
  promuove V3. Una successiva approval di A non puo distribuire V2, perche
  V1 non e piu il champion corrente. Il numero della versione, il commit e
  l'alias candidate non sostituiscono questo controllo.
- Il gruppo protegge i job che lo adottano nello stesso repository, non le
  operazioni CLI esterne. Non eseguire deploy o modifiche manuali a champion
  durante un deploy della pipeline. Il controllo seguito dalla promozione
  non e una compare-and-swap atomica del Registry.
- Un errore dopo l'applicazione puo lasciare il servizio modificato anche se
  champion resta invariato: questa politica non introduce rollback automatico.
  Prima di riprendere i deploy occorre verificare e ripristinare manualmente
  la coerenza tra servizio e champion.

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
  Richiede il run di training FINISHED, commit e hash del CSV coerenti con
  lo snapshot e appartenenza del modello loggato a quel run esatto.
  Non ripete split o preprocessing e non accetta nuovi hash come sostituti
  dei valori attesi. File mancanti, alterazioni o riferimenti incoerenti
  causano errore esplicito.
- Il training non applica il gate. L'evaluation salva report separato e
  artifact nello stesso run, senza riaprirlo; un gate negativo fa fallire
  soltanto lo step di evaluation. Il riepilogo legge entrambi i risultati
  e viene prodotto anche per un confronto concluso con esito negativo.
- Report con identità di modelli/test set, entrambi i RMSE ed esito. Lo
  snapshot non garantisce che champion sia ancora servito al momento del deploy.
- Nuovo push/workflow o riesecuzione job acquisisce un nuovo snapshot;
  niente riuso implicito del report precedente. Versione e run attempt rendono
  riconoscibile un cambio di confronto.
- Le prove PR accettano anche un candidato equivalente al champion;
  non richiedono un miglioramento stretto. Le prove di cambio alias e di
  bootstrap usano test isolati, senza alterare gli alias reali.

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

Le soglie riprendono il lab Azure ML e sono state confrontate con due run
locali già riusciti nella ricognizione del 2026-11-09: R2 circa 0.850/0.859
e RMSE circa 250.28/242.38. Sono una policy iniziale del laboratorio, non
una certificazione generale della qualità del modello. Il bootstrap è
stato verificato con test isolati, non cancellando il champion reale.

## Tag e identità immagini

### Ruolo dei tre componenti

| Immagine | Contenuto e consumer | Dove viene eseguita | Caricamento KinD |
|---|---|---|---|
| gp-mof-capstone-training | Sorgenti e dipendenze di training; launcher run_training.py | Docker locale, rete mlops-local-network, CSV montato read-only | No |
| gp-mof-capstone-deployment-resolver | Codice, template e configurazione del resolver; render.sh | Docker locale, rete mlops-local-network, output persistito tramite bind mount | No |
| gp-mof-capstone-mlserver | MLServer, dipendenze e LoggingMLflowRuntime; ServingRuntime | Pod predictor KServe nel cluster | Sì, con kind load |

- Il [Dockerfile training](../training/Dockerfile) copia i sorgenti, non il
  dataset. Il launcher passa rete Docker, mount dati e provenance.
- Il [Dockerfile resolver](../deployment/Dockerfile) incorpora anche template
  e configurazione. Il [consumer render.sh](../deployment/scripts/render.sh)
  condivide DEPLOYMENT_RESOLVER_IMAGE con il build script: build e consumer
  devono usare lo stesso riferimento verificato.
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
  propagare il riferimento effettivo al relativo consumer. Nella pipeline
  corrente le tre immagini sono preparate dal commit del job. Il rollback
  manuale ripristina invece immagine e modello del rilascio selezionato,
  senza ricostruire il runtime dal commit corrente.

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
