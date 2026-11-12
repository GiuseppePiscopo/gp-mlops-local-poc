# Piano di implementazione CI/CD locale

Documento temporaneo per avanzamento e ripresa delle sessioni. I contratti e
le evidenze sono in [design.md](design.md); la procedura operativa in costruzione
è [github-actions-self-hosted-wsl.md](github-actions-self-hosted-wsl.md).

## Indice della progettazione

- [Architettura e perimetro](design.md#architettura-e-perimetro)
- [Configurazione e accessi del runner](design.md#configurazione-e-accessi-del-runner)
- [PR fidate e permessi](design.md#pr-fidate-e-permessi)
- [Stato iniziale](design.md#stato-iniziale)
- [Training, evaluation e registrazione](design.md#training-evaluation-e-registrazione)
- [Identificazione e passaggio tra job](design.md#identificazione-e-passaggio-tra-job)
- [Confronto e prove PR](design.md#confronto-e-prove-pr)
- [Bootstrap ed evidenze locali](design.md#bootstrap-ed-evidenze-locali)
- [Tag e identità immagini](design.md#tag-e-identità-immagini)
- [Riferimento Azure ML](design.md#riferimento-azure-ml)
- [Accesso operativo e port-forward](design.md#accesso-operativo-e-port-forward)

## 1. Definire i contratti di training ed evaluation

- [x] Ispezionare e documentare metriche e comportamento correnti.
- [x] Concordare gate relativo: RMSE candidato <= champion, parità ammessa.
- [x] Definire logging separato dalla registrazione post-gate, esclusa nelle PR.
- [x] Definire identificazione di run/modello e versione esatta tra i job.
- [x] Definire bootstrap manuale e casi di test; verificare soglie sui run locali.
- [x] Definire snapshot del riferimento e ripetibilità delle prove PR.

## 2. Preparare e provare i tag immutabili

- [x] Definire tag per commit, image ID locale e RepoDigest opzionale.
- [x] Valutare separatamente training, resolver e MLServer: soltanto il runtime
  serving deve essere caricato in KinD.
- [x] Implementare nei build script e consumer di training/resolver verifica sorgenti, tag non
  sovrascrivibili, riuso verificato e registrazione delle identità; aggiornare
  le procedure di training e resolver.
- [x] Verificare la regressione di training e deployment resolution in WSL,
  prima di modificare MLServer: sincronizzare gli script aggiornati, costruire
  dal commit pulito le due immagini e ripetere la build per verificarne il
  riuso senza cambio di image ID. Eseguire un training reale e controllare
  metriche, artifact, nuova Model Version READY e provenance immagine su
  MLflow, senza spostare gli alias esistenti. Renderizzare champion con il
  resolver aggiornato e verificare manifest/lock rispetto alla versione
  attesa, senza applicarli al cluster. La prova crea un run e una versione:
  registrare gli identificativi e l'esito, senza dichiararla priva di effetti.
- [x] Integrare il build MLServer con lo script comune e rendere configurabile
  l'immagine del ServingRuntime mantenendo l'uso manuale. Adottare insieme
  build e consumer per non interrompere la procedura serving corrente.
- [x] Aggiornare la procedura serving: build, kind load e verifica del tag SHA.
- [x] Provare una volta nell'ambiente WSL/Docker/KinD esistente e verificare
  l'immagine effettivamente avviata dal predictor.
- [x] Preservare il cluster e il servizio; niente ricreazioni o modifiche
  distruttive. Eventuale ripristino esplicito del riferimento precedente.

## 3. Preparare runner e sicurezza

- [x] Configurare il remote GitHub aggiuntivo; rendere esplicita la destinazione
  dei push per non usare accidentalmente Bitbucket.
- [x] Configurare runner WSL, etichetta e accesso al solo repository previsto.
- [x] Verificare Git LFS in WSL, Docker, kind, kubectl, MLflow, SeaweedFS e dataset runtime.
- [x] Definire configurazioni persistenti, variabili e segreti senza esporli.
- [x] Usare il checkout Linux e il relativo commit per build e provenance.
  Verificare checkout, build e riuso con il
  [workflow Verify runner](github-actions-self-hosted-wsl.md#verificare-checkout-e-build-sul-runner).
- [x] Definire PR fidate e permessi minimi; niente esecuzione del codice PR con
  pull_request_target. Non considerare i filtri una barriera di isolamento.

## 4. Implementare workflow e job

- [x] Creare `ml-pipeline.yml` con trigger PR, push su main e avvio manuale;
  PR senza deploy. Mantenere separato il workflow diagnostico `verify-runner.yml`.
  Applicare il perimetro [PR fidate e permessi](design.md#pr-fidate-e-permessi).
- [x] Implementare training dal checkout con immagine per commit e dati runtime,
  usando [configurazione e accessi del runner](design.md#configurazione-e-accessi-del-runner).
- [x] Separare logging da registrazione nel codice/launcher; aggiornare CLI
  e procedura manuale, mantenendo utilizzabile il percorso non orchestrato.
- [x] Implementare snapshot champion, identità del test set e gate relativo.
- [x] Separare preparazione, training ed evaluation in step dello stesso job,
  secondo [Confronto e prove PR](design.md#confronto-e-prove-pr):
  - [x] Acquisire prima del training snapshot champion e copia CSV per esecuzione,
    fuori dal checkout, con SHA-256 fissato e mount in sola lettura.
  - [x] Salvare dal training il test set effettivo con feature, target, indici,
    ordine e tipi in storage effimero del runner, con pulizia a fine job;
    conservare in MLflow solo manifest, identità e checksum associati al run
    e al dataset iniziale.
  - [x] Eseguire il gate in uno script dedicato nella stessa immagine training,
    usando risultati espliciti del training, snapshot champion e test set verificato;
    non rileggere alias o CSV runtime e non ripetere split/preprocessing.
  - [x] Aggiornare launcher, workflow e test; verificare alterazioni di CSV/test set,
    parità, regressione ed esportazione del report anche a gate fallito.
- [x] Implementare bootstrap esplicito con soglie assolute e controlli dedicati.
- [x] Produrre risultati JSON e report come artifact della stessa esecuzione,
  predisporre gli output del job con identità del candidato e del gate;
  pubblicare metriche ed esiti nel riepilogo del workflow.
- [x] Registrare solo post-gate su main/manuale e assegnare candidate READY;
  aggiungere agli output la versione esatta per il job di deploy.
- [x] Estendere resolver, CLI, lock e documentazione per versione esplicita,
  preservando il rendering manuale per alias.
- [x] Definire serializzazione dei deploy e rifiuto dei rilasci obsoleti
  approvati fuori ordine, includendo il caso bootstrap.
- [x] Configurare environment production con approval e restrizione a main;
  verificare le protezioni dei workflow da PR esterne nel repository pubblico.
  Vedere [Architettura e perimetro](design.md#architettura-e-perimetro) e
  [PR fidate e permessi](design.md#pr-fidate-e-permessi).
- [x] Aggiungere il job deploy dipendente da training/evaluation e collegato
  all'environment production, escluso dalle PR e dai gate falliti.
  Implementata la sola approval con riepilogo del target; nessun rilascio.
- [x] Prima di abilitare modifiche al servizio, implementare concurrency e
  controllo champion post-approval secondo
  [Serializzazione e approvazioni fuori ordine](design.md#serializzazione-e-approvazioni-fuori-ordine).
  Verificare con test isolati champion invariato, cambiato, assente, errori
  Registry e bootstrap con champion comparso.
- [x] Renderizzare la versione esatta, caricare runtime in KinD e applicare
  risorse/InferenceService.
- [x] Attendere readiness, provare inferenza e aggiornare champion solo al successo.
- [x] Conservare report, manifest, lock, riferimenti modello e immagini come
  output/artifact della stessa esecuzione, senza credenziali.

## 5. Verificare i percorsi

- [x] PR migliorativa: gate superato, nessuna registrazione o deploy.
- [x] Seconda PR regressiva: gate fallito; correzione sulla stessa PR e nuovo
  push: gate superato. Controllare riferimenti e dati dei report.
- [x] Main e manuale: registrazione post-gate, attesa approval e deploy riuscito.
- [x] Rifiuto/mancata approval: nessun deploy o aggiornamento champion.
- [x] Errori di gate/render/immagine/readiness/inferenza: fallimento esplicito
  e champion invariato, senza dichiarare rollback automatico.
- [x] Test isolati di soglie bootstrap, errori Registry, champion presente o
  comparso durante approval; non cancellare l'alias reale.
- [x] Test isolato: cambio alias dopo snapshot non cambia il confronto fissato.
- [x] Verificare identità tra modello valutato, registrato e servito e immagini.
- [x] Verificare e documentare rollback manuale, senza automatizzarlo.

## 6. Documentare e chiudere

- [x] Scrivere `github-actions-self-hosted-wsl.md`: prerequisiti, runner,
  protezioni GitHub, configurazione, trigger, comandi, immagini, verifiche,
  troubleshooting, sicurezza e rollback manuale.
- [ ] Aggiornare [roadmap.md](../roadmap.md) solo dopo verifica della CI/CD.
- [ ] Allineare procedura e roadmap ai risultati effettivi; trasferire le
  decisioni ancora utili dal design nella documentazione stabile.
- [ ] Rimuovere questo piano quando procedura e roadmap sono complete;
  mantenere il design solo per decisioni non già spiegate nella procedura.

## Ripresa della sessione

Leggere prima questo piano e poi solo le sezioni del design necessarie.
Controllare Git e stato effettivo prima di spuntare attività. Per verifiche
sull'ambiente controllare contesto Kubernetes, alias/versioni e disponibilità
dei port-forward, senza avviare duplicati o arrestare processi dell'utente.
Aggiornare le checkbox per indicare l'avanzamento, nel design soltanto contratti
ed evidenze. Non duplicare i dettagli nella checklist. Procedere un punto alla
volta e fermarsi dopo ogni step con modifiche al repository per valutazione e
possibile commit dell'utente.
