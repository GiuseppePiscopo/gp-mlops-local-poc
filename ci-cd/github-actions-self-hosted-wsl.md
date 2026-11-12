# CI/CD GitHub Actions con runner WSL

Questa procedura configura un runner GitHub Actions nel laboratorio WSL con
Docker e KinD, verifica checkout e build delle immagini ed esegue training
ed evaluation del modello `gp-mof-capstone-model` con MLflow, quindi applica
la versione approvata al cluster KinD, verifica l'inferenza e la promuove
a `champion`.

## Prerequisiti

- Ubuntu WSL2 x64, utente non root, Docker Desktop con integrazione WSL;
- Git, Git LFS, `python3` >= 3.11, `kind`, `kubectl` e `curl` disponibili in WSL;
- repository già configurato per LFS, con file LFS scaricati;
- repository GitHub `GiuseppePiscopo/gp-mlops-local-poc` pubblico, con accesso
  amministratore;
- [stack MLflow](../mlflow/mlflow-docker-wsl-setup.md) attivo nella rete
  `mlops-local-network`;
- [dataset di training](../training/training-docker-wsl-setup.md#preparare-il-dataset)
  disponibile in `/opt/mlops-local-poc/training/data/input/SeoulBikeData.csv`;
- cluster `mlops-test`, contesto `kind-mlops-test` e
  [serving KServe](../serving/serving-kserve-model.md) operativo;
- credenziali locali in `/opt/mlops-local-poc/mlflow/.env` e kubeconfig
  dell'utente WSL in `$HOME/.kube/config`.

Tutti i comandi si eseguono in **Bash in WSL**, salvo le operazioni indicate
nell'interfaccia GitHub.

## Avvertenze operative

- Eseguire sul runner soltanto codice fidato.
- Non approvare workflow di PR esterne: possono modificare i filtri del
  workflow ed eseguire codice sul runner self-hosted.
- Non pubblicare `.env`, kubeconfig, credenziali del runner o contenuti del
  Secret, neppure come log o artifact.
- Non esportare globalmente `IMAGE_TAG` o `GIT_REPOSITORY` nel runner.
- Prima di cambiare la visibilità del repository, verificare le protezioni
  per le PR e la disponibilità dell'approval dell'environment `production`.

## Verificare i permessi GitHub Actions

Nel repository GitHub, aprire **Settings > Actions > General > Workflow
permissions** e verificare:

- **Read repository contents and packages permissions** selezionato;
- **Allow GitHub Actions to create and approve pull requests** disabilitato.

Salvare se sono state apportate modifiche. Atteso: permessi predefiniti in
sola lettura e approvazione delle PR da workflow non consentita.

Nella stessa pagina, in **Fork pull request workflows from outside
collaborators**, selezionare **Require approval for all outside collaborators**
e salvare.

## Configurare l'environment production

Aprire **Settings > Environments > New environment**, inserire `production`
e premere **Configure environment**. Se esiste gia, aprirlo.

In **Deployment protection rules**:

- abilitare **Required reviewers** e aggiungere `GiuseppePiscopo`;
- lasciare **Prevent self-review** disabilitato;
- disabilitare **Allow administrators to bypass configured protection rules**;
- salvare le regole.

In **Deployment branches and tags**, selezionare **Selected branches and tags**
e aggiungere una sola regola di tipo **Branch** con nome `main`. Non aggiungere
regole per i tag.

Verificare reviewer, self-review consentita, bypass disabilitato e unica
regola per il branch `main`. Non aggiungere secrets o variables.

## Configurare il remote GitHub

Eseguire i comandi Git in PowerShell dalla root del repository Windows.
Verificare i remote:

```powershell
git remote -v
```

Se manca `github`, aggiungerlo:

```powershell
git remote add github https://github.com/GiuseppePiscopo/gp-mlops-local-poc.git
```

Verificare la destinazione dei push:

```powershell
git remote get-url --push github
git remote get-url --push origin
```

Atteso: `github` punta a `https://github.com/GiuseppePiscopo/gp-mlops-local-poc.git`;
`origin` mantiene l'URL Bitbucket esistente.

Per inviare modifiche a GitHub, specificare sempre il remote:

```powershell
git push github main
```

## Registrare il runner

Nel repository GitHub, aprire **Settings > Actions > Runners > New self-hosted
runner** e scegliere **Linux / x64**.

Preparare la directory nel filesystem Linux:

```bash
mkdir -p ~/actions-runner
cd ~/actions-runner
```

Eseguire i comandi GitHub di download, verifica checksum ed estrazione, senza
ripetere la creazione della directory. La verifica checksum deve terminare
con `OK`.

Eseguire il comando `./config.sh` mostrato da GitHub e usare:

| Campo                      | Valore        |
| -------------------------- | ------------- |
| Runner group, se richiesto | predefinito   |
| Runner name                | `mlops-wsl`   |
| Additional labels          | `mlops-local` |
| Work folder                | `_work`       |

Usare il token temporaneo solo nel terminale locale; non salvarlo nel
repository, nei documenti o nei log condivisi.

Verificare in **Settings > Actions > Runners** la presenza di `mlops-wsl`,
con etichette `self-hosted`, `Linux`, `X64`, `mlops-local`.

## Avviare il runner

Avviare il runner e lasciare aperto il terminale:

```bash
cd ~/actions-runner
./run.sh
```

Atteso: `Listening for Jobs` nel terminale e runner `Idle` su GitHub.
Terminare con `Ctrl+C` solo quando non ci sono job in esecuzione.

## Verificare gli accessi locali

In un secondo terminale, con lo stesso utente WSL del runner, verificare
lettura e permessi dei file locali senza stamparne il contenuto:

```bash
test -r /opt/mlops-local-poc/mlflow/.env
test -r "$HOME/.kube/config"
stat -c '%a %U %n' /opt/mlops-local-poc/mlflow/.env "$HOME/.kube/config"
```

Atteso: entrambi i `test` terminano senza errori; `stat` mostra permessi `600`
e proprietario corrispondente all'utente del runner.

Verificare il dataset, Docker e il contesto Kubernetes:

```bash
test -r /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv
sha256sum /opt/mlops-local-poc/training/data/input/SeoulBikeData.csv

docker info --format '{{.ServerVersion}}'

kubectl --context kind-mlops-test get nodes
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get inferenceservice gp-mof-capstone-model
```

Atteso: dataset leggibile e hash uguale a quello verificato nella preparazione
del training; versione Docker stampata, nodo `Ready` e servizio `READY=True`.

Per aggiornare il Secret S3 e verificarne l'endpoint, seguire
[Applicare il Secret S3](../serving/serving-kserve-model.md#applicare-il-secret-s3).

## Verificare checkout e build sul runner

Inviare al branch `main` di GitHub i commit da verificare:

```powershell
git push github main
```

Con il runner attivo, aprire **Actions > Verify runner > Run workflow**
([verify-runner.yml](../.github/workflows/verify-runner.yml)),
selezionare `main` e avviare l'esecuzione.

### Verificare l'esecuzione su GitHub

Aprire l'esecuzione e verificare il successo del job **Verify runner**.

Nel **Summary**, controllare la sezione **Linux checkout and image**:

| Campo    | Valore atteso                                                            |
| -------- | ------------------------------------------------------------------------ |
| Commit   | SHA completo del commit selezionato per l'esecuzione                     |
| Image    | `gp-mof-capstone-training:git-<Commit>`, con lo SHA del campo precedente |
| Image ID | `sha256:` seguito da 64 caratteri esadecimali                            |
| Reused   | `False` per una nuova immagine; `True` se era già presente e verificata  |

### Verificare il checkout in WSL

Cliccare sul job **Verify runner** per aprire il dettaglio coi log, aprire lo step
**Verify Linux checkout and commit**, copiare il percorso stampato dopo `Checkout:`.
In un secondo terminale WSL, impostarlo ed eseguire:

```bash
export RUNNER_CHECKOUT="<percorso Checkout riportato nel log>"

git -C "$RUNNER_CHECKOUT" rev-parse --show-toplevel
findmnt --noheadings --output FSTYPE --target "$RUNNER_CHECKOUT"
git -C "$RUNNER_CHECKOUT" rev-parse HEAD
git -C "$RUNNER_CHECKOUT" status --short
git -C "$RUNNER_CHECKOUT" lfs fsck
```

Atteso, senza altre esecuzioni nel frattempo:

- root uguale a `RUNNER_CHECKOUT`, sotto `~/actions-runner/_work/`;
- filesystem `ext4`, `xfs` o `btrfs`;
- commit uguale al campo **Commit** del Summary;
- nessun output da `status --short`;
- `Git LFS fsck OK`.

### Verificare il riuso dell'immagine

Ripetere **Run workflow** su `main`, senza nuovi push o modifiche al branch.
Nel nuovo Summary verificare:

- **Commit**, **Image** e **Image ID** uguali alla prima esecuzione;
- **Reused** uguale a `True`.

Questa verifica costruisce soltanto l'immagine: non esegue training,
registrazione di modelli o deploy.

## Avviare training ed evaluation

Con il runner attivo, aprire **Actions > ML pipeline > Run workflow** e
selezionare `main`. Lasciare **Initial model only...** non selezionato e
avviare l'esecuzione.

Aprire l'esecuzione e verificare il successo del job **Train and evaluate**.

Nel **Summary**, verificare:

- **Training result**: run MLflow, URI del modello e commit dell'esecuzione;
- **Relative evaluation**: modello `gp-mof-capstone-model`, versione e run
  champion, metriche dei due modelli e `Passed` uguale a `True`.
- **Model registration**: modello `gp-mof-capstone-model`, nuovo numero di
  versione, stato `READY` e alias `candidate`.

Aprire in MLflow il run indicato nel Summary e verificare:

- stato `FINISHED`;
- nella cartella artifact `evaluation`, file `report.json`, con `mode` uguale a `relative` e
  `passed` uguale a `true`;
- tag `evaluation.passed` uguale a `true`;
- RMSE del candidato minore o uguale a quello del champion nel report.

Prima di approvare il deployment, aprire **Models > gp-mof-capstone-model**
in MLflow e selezionare la versione
indicata nella sezione **Model registration** del Summary. Verificare:

- alias `candidate` assegnato alla nuova versione;
- run sorgente uguale al run della sezione **Training result**;
- source uguale all'URI `models:/m-<model ID>` della sezione **Training result**;
- alias `champion` e `baseline` ancora sulle versioni precedenti.

Nella sezione **Artifacts** del riepilogo dell'esecuzione GitHub, scaricare
`training-evaluation-<run ID GitHub>-<run attempt>` e verificare:

- `training-result.json`: stessi run MLflow, URI del modello e commit del Summary;
- `evaluation-report.json`: stesso confronto dell'artifact MLflow
  `evaluation/report.json`, con `passed` uguale a `true`;
- `execution.json`: ID e attempt dell'esecuzione GitHub corrente, commit,
  riferimenti al candidato, `evaluation_mode` uguale a `relative` ed
  `evaluation_passed` uguale a `true`; `registered_model` e `model_version`
  uguali al modello e alla versione del Summary;
- `registration-result.json`: stessi run, URI e commit di `training-result.json`,
  modello `gp-mof-capstone-model`, versione registrata e `status` uguale a `READY`.

Atteso: l'archivio contiene soltanto i quattro JSON, senza `test-data.json`.

## Approvare e applicare il deployment

### Approvare il workflow

Nel dettaglio dell'esecuzione GitHub, verificare che **Train and evaluate**
sia completato con successo e **Deploy** sia in attesa di
review per l'environment `production`.

Non occorre avviare un port-forward al servizio di inferenza per questa
verifica. Durante il job non eseguire deploy o modifiche manuali a champion.

Aprire **Review deployments**, selezionare `production` e premere
**Approve and deploy**.

### Verificare il deployment

Verificare il successo del job **Deploy** e, nel Summary,
la sezione **Production approval**:

- modello e versione uguali alla sezione **Model registration**;
- run sorgente, URI del modello e commit uguali alla sezione **Training result**;
- indicazione del completamento dei controlli pre-deploy.

Nella sezione **Deployment apply**, verificare:

- modello e versione uguali alla sezione **Model registration**;
- contesto `kind-mlops-test`;
- immagine runtime `gp-mof-capstone-mlserver:git-<commit>` con il commit della
  sezione **Training result**, e relativo image ID;
- indicazione dei manifest applicati.

Nella sezione **Deployment verification**, verificare:

- modello e versione uguali alla sezione **Model registration**;
- nome del pod predictor verificato e relativo **Running image ID**;
- numero di righe della prova e altrettante predizioni numeriche finite;
- **Champion** uguale alla versione distribuita, con la precedente versione
  indicata tra parentesi (`absent` nel bootstrap);
- indicazione del completamento di readiness, inferenza e promozione.

Non confrontare direttamente **Running image ID** con l'image ID Docker
di **Deployment apply**.

In un secondo terminale WSL, controllare la risorsa applicata:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get inferenceservice gp-mof-capstone-model \
  -o jsonpath='{.metadata.annotations.mlops\.gp-mof\.dev/resolved-version}{"\n"}{.metadata.annotations.mlops\.gp-mof\.dev/source-run-id}{"\n"}{.spec.predictor.model.storageUri}{"\n"}{.spec.predictor.model.env}{"\n"}'
```

Verificare le quattro righe stampate, nell'ordine:

1. Versione uguale a quella della sezione **Model registration** del Summary.
2. Run sorgente uguale a quello della sezione **Training result**.
3. URI S3 uguale a **Storage URI** nella sezione **Deployment apply**.
4. Elenco delle variabili d'ambiente con `MODEL_NAME` uguale a
   `gp-mof-capstone-model` e `MODEL_VERSION` uguale alla versione della prima riga.

Controllare l'immagine del runtime applicato:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get servingruntime gp-mof-capstone-mlserver \
  -o jsonpath='{.spec.containers[0].image}{"\n"}'
```

Verificare che l'unica riga stampata sia uguale a **Runtime image** nella
sezione **Deployment apply** del Summary.

In MLflow, aprire **Models > gp-mof-capstone-model** e verificare che
`champion` punti alla versione della sezione **Model registration**.
Controllare anche che il deploy non abbia modificato `baseline` o `candidate`.

### Verificare gli artefatti

Nella sezione **Artifacts** dell'esecuzione GitHub, scaricare
`deployment-<run ID GitHub>-<run attempt>` e verificare i sei file:

- `gp-mof-capstone-model-v<versione>.yaml`: versione, run e URI S3 uguali
  alle verifiche precedenti sull'InferenceService;
- `gp-mof-capstone-model-v<versione>.resolution.json`: stessa versione e
  provenance del manifest, con `requested_version` valorizzato e
  `requested_alias` uguale a `null`;
- `20-serving-runtime.yaml`: immagine uguale a **Runtime image** del Summary;
- `deployment-result.json`: `status` uguale a `APPLIED`, modello, versione,
  run, URI e commit uguali al Summary; riferimenti e identità delle immagini
  in `images`, identità dell'immagine caricata nei nodi in `kind_image`;
- `verification-result.json`: `status` uguale a `VERIFIED`, stessi riferimenti
  del modello, pod e image ID uguali a **Deployment verification**, numero
  di righe e predizioni uguali al Summary;
- `release-result.json`: `status` uguale a `DEPLOYED`, stessi dati della
  verifica e `promotion.status` uguale a `PROMOTED`, con versione e champion
  precedente uguali al Summary.

Atteso: nessun Secret, kubeconfig, credenziale, esempio di input o payload
di inferenza nell'archivio.

Confrontare modello, versione, run, URI e commit dei risultati di deployment
con `registration-result.json`, `training-result.json` ed
`evaluation-report.json` nell'artifact `training-evaluation` della stessa
esecuzione. Atteso: tutti i riferimenti coincidono.

Se il job fallisce, controllare il servizio con i comandi precedenti e
l'alias `champion` in MLflow prima di riprovare. Non presumere un ripristino
automatico.

## Verificare il workflow su PR e merge

Creare un branch da `github/main` e aggiungere un commento dummy a un file
del training. La modifica non deve peggiorare il comportamento del modello.
Usare una PR dallo stesso repository, aperta con l'account
`GiuseppePiscopo`. Eseguire i comandi Git e GitHub CLI in PowerShell dalla
root del repository Windows.

```powershell
git fetch github

git switch -c feature/pr-trigger-check-1 --no-track github/main

Add-Content .\training\src\random_forest\train.py "`n# trigger check nr 1"
git commit -am "chore(ci-cd): verifica scenario con PR e merge nr 1"

git push --set-upstream github feature/pr-trigger-check-1

gh pr create --repo GiuseppePiscopo/gp-mlops-local-poc --base main --head feature/pr-trigger-check-1 --title "Verifica trigger CI su PR nr 1" --body "Modifica dummy per verificare il workflow su PR."
```

In **Actions**, aprire la run della PR. Verificare che l'evento sia
`pull_request` e attendere il completamento. Il gate relativo passa se l'RMSE
del candidato è minore o uguale a quello di `champion`; non è necessario che
il candidato migliori le metriche.

Verificare che:

- il job **Train and evaluate** termini con successo e il Summary riporti
  **Relative evaluation** con `Passed` uguale a `True`;
- non venga eseguito il passo **Register evaluated model as candidate**;
- il job **Deploy** non venga eseguito.

Scaricare l'artifact `training-evaluation-<run ID GitHub>-<run attempt>` e
verificare che contenga soltanto:

- `training-result.json`;
- `evaluation-report.json`, con `passed` uguale a `true`;
- `execution.json`, con `evaluation_passed` uguale a `true` e senza
  `registered_model` o `model_version`.

In MLflow, verificare che la run della PR non abbia creato una nuova Model
Version e che gli alias `candidate`, `champion` e `baseline` siano invariati.

Dopo aver verificato il run della PR, eseguire il merge senza bypassare gli
eventuali controlli di protezione del branch:

```powershell
gh pr merge feature/pr-trigger-check-1 --repo GiuseppePiscopo/gp-mlops-local-poc --squash --delete-branch
```

In **Actions**, aprire il nuovo run su `main` e verificare che l'evento sia
`push`. Attendere il completamento del job **Train and evaluate** e
controllare che il gate relativo abbia `Passed` uguale a `True` e che la
sezione **Model registration** riporti la nuova versione `candidate`.
Verificare che il job **Deploy** attenda l'approvazione per l'environment
`production`; quindi completare le verifiche e l'approvazione indicate in
[Approvare e applicare il deployment](#approvare-e-applicare-il-deployment).

A deployment completato, sincronizzare `main` locale e rimuovere il branch
locale dello scenario:

```powershell
git switch main
git pull --ff-only github main
git branch -D feature/pr-trigger-check-1
```

## Verificare una PR regressiva

In MLflow, annotare le versioni degli alias `candidate`, `champion` e
`baseline`. Annotare anche la versione indicata dall'InferenceService.

Creare un branch da `github/main` e ridurre temporaneamente i parametri del
training nel workflow. Aprire la PR dallo stesso repository con l'account
`GiuseppePiscopo`.

```powershell
git fetch github

git switch -c experiment/tune-model-1 --no-track github/main

(Get-Content .\.github\workflows\ml-pipeline.yml) `
  -replace '--n-estimators 100', '--n-estimators 1' `
  -replace '--max-depth 10', '--max-depth 1' |
  Set-Content .\.github\workflows\ml-pipeline.yml

git commit -am "test(ci-cd): degrada intenzionalmente il modello nr 1"

git push --set-upstream github experiment/tune-model-1

gh pr create --repo GiuseppePiscopo/gp-mlops-local-poc --base main --head experiment/tune-model-1 --title "Verifica gate regressivo" --body "Verifica il rifiuto del candidato e il ripristino sulla stessa PR."
```

In **Actions**, aprire la run `pull_request` del branch e attendere il
completamento. Verificare che **Train and evaluate** fallisca al passo
**Evaluate candidate** e che il Summary riporti **Relative evaluation** con
`Passed` uguale a `False`.

Scaricare `training-evaluation-<run ID GitHub>-<run attempt>` e verificare
che contenga i tre JSON:

- `training-result.json`;
- `evaluation-report.json`, con RMSE candidato maggiore del RMSE champion e
  `passed` uguale a `false`;
- `execution.json`, con `evaluation_passed` uguale a `false` e senza
  `registered_model` o `model_version`.

Verificare che il run MLflow riporti `evaluation.passed` uguale a `false`, che
non sia stata creata una nuova Model Version e che gli alias e la versione
dell'InferenceService siano invariati. Se il gate passa, non eseguire il merge.

## Correggere una PR regressiva

Correggere gli stessi parametri nella stessa PR e aggiungere un commento
dummy al training:

```powershell
(Get-Content .\.github\workflows\ml-pipeline.yml) `
  -replace '--n-estimators 1(?=\s|$)', '--n-estimators 100' `
  -replace '--max-depth 1(?=\s|$)', '--max-depth 10' |
  Set-Content .\.github\workflows\ml-pipeline.yml

Add-Content .\training\src\random_forest\train.py "`n# regression recovery check nr 1"

git commit -am "fix(ci-cd): ripristina i parametri del modello nr 1"

git push github experiment/tune-model-1
```

Attendere la nuova run `pull_request` del branch. Verificare che
**Train and evaluate** termini con successo, che **Relative evaluation**
riporti `Passed` uguale a `True` e che la PR non esegua registrazione o deploy.

Scaricare l'artifact della nuova run e verificare i tre JSON. In
`evaluation-report.json`, controllare che `passed` sia `true` e che i
riferimenti del candidato coincidano con `training-result.json`. Confrontare
`champion` e i checksum del test set con quelli del report della run regressiva.
In `execution.json`, controllare il run ID GitHub, l'attempt, il commit e
`evaluation_passed` uguale a `true`, senza `registered_model` o
`model_version`. Verificare che gli alias e la versione dell'InferenceService
siano ancora invariati.

Eseguire il merge:

```powershell
gh pr merge experiment/tune-model-1 --repo GiuseppePiscopo/gp-mlops-local-poc --squash --delete-branch
```

Attendere il nuovo run `push` su `main`.

Verificare il superamento del gate, la registrazione della nuova versione
`candidate` e l'attesa dell'approvazione per `production`. Completare le
verifiche e l'approvazione indicate in
[Approvare e applicare il deployment](#approvare-e-applicare-il-deployment).

A deployment completato, sincronizzare `main` locale e rimuovere il branch
locale dello scenario:

```powershell
git switch main
git pull --ff-only github main
git branch -D experiment/tune-model-1
```

## Verificare l'attesa e il rifiuto dell'approvazione

Prima di avviare lo scenario, verificare che non ci siano altri workflow in
esecuzione o deployment in attesa di approvazione. Non eseguire deploy o
modifiche manuali agli alias durante la verifica.

In MLflow, annotare le versioni degli alias `candidate`, `champion` e
`baseline` del modello `gp-mof-capstone-model`.

In un terminale WSL, eseguire i comandi di controllo dell'InferenceService e
del ServingRuntime indicati in
[Verificare il deployment](#verificare-il-deployment). Annotare versione,
run sorgente, URI S3 e immagine runtime.

Avviare una nuova esecuzione manuale su `main` e completare le verifiche di
[Avviare training ed evaluation](#avviare-training-ed-evaluation), senza
approvare il deployment.

### Verificare l'attesa dell'approvazione

Aprire l'esecuzione GitHub e verificare che **Train and evaluate** sia
completato con successo e **Deploy** sia in attesa della review per
`production`.

In MLflow, verificare che:

- esista la nuova Model Version indicata in **Model registration**;
- `candidate` punti alla nuova versione;
- `champion` e `baseline` puntino ancora alle versioni annotate.

Ripetere i controlli dell'InferenceService e del ServingRuntime. Verificare
che versione, run sorgente, URI S3 e immagine runtime siano invariati.

### Rifiutare il deployment

Nel dettaglio della stessa esecuzione GitHub, aprire **Review deployments**,
selezionare `production`, inserire un commento per lo scenario e premere
**Reject**.

Attendere la conclusione della run e verificare che:

- il job **Train and evaluate** sia ancora concluso con successo;
- il deployment risulti rifiutato e il job **Deploy** non abbia eseguito
  i passi di controllo, apply, verifica o promozione;
- sia presente l'artifact `training-evaluation-<run ID GitHub>-<run attempt>`;
- non sia presente l'artifact `deployment-<run ID GitHub>-<run attempt>`.

In MLflow, verificare che la nuova Model Version sia ancora `READY` e che
`candidate` punti ancora a essa. Verificare che `champion` e `baseline` siano
invariati rispetto all'inizio dello scenario.

Ripetere i controlli dell'InferenceService e del ServingRuntime. Verificare
che versione, run sorgente, URI S3 e immagine runtime coincidano con quelli
annotati prima dell'esecuzione.

## Verificare gli errori di deployment con test isolati

Questa prova simula gli errori tramite test isolati, senza provocare guasti
nel cluster reale.

In PowerShell su Windows, dalla root del repository, eseguire:

```powershell
python -m unittest discover -s .\ci-cd\tests -p "test_*deployment.py" -k DeploymentTests -v
```

Verificare che tutti i test selezionati terminino con `ok` e che il
riepilogo finale riporti `OK`, senza errori o fallimenti.

Controllare nell'elenco i casi di:

- risoluzione, lock o manifest non coerenti con il modello approvato;
- errore nella costruzione o nel caricamento dell'immagine e nell'apply;
- rollout incompleto o immagine in esecuzione diversa da quella verificata;
- errore Kubernetes o HTTP, risposta di inferenza non valida o riavvio del pod;
- mancata promozione dopo un errore e assenza di un risultato di rilascio
  dichiarato riuscito.

Per il gate fallito, usare l'esito già verificato in
[Verificare una PR regressiva](#verificare-una-pr-regressiva).

## Verificare bootstrap e controlli Registry con test isolati

In MLflow, aprire l'ultimo run di training e copiare il valore del tag
`container.image`.

In un terminale WSL, impostare il percorso del repository Windows e
l'immagine copiata:

```bash
export GIT_REPOSITORY="/mnt/c/<percorso-repository>/mlops-local-poc"
export IMAGE_TAG="<valore del tag container.image>"
docker image inspect "$IMAGE_TAG" --format '{{.Id}}'
```

Verificare che l'immagine sia presente. Eseguire i test senza collegare il
container alla rete del laboratorio e senza montare credenziali:

```bash
docker run --rm --network none \
  --mount "type=bind,src=$GIT_REPOSITORY,dst=/workspace,readonly" \
  --workdir /tmp \
  -e PYTHONPATH=/workspace/training/src:/workspace/training/tests \
  --entrypoint python \
  "$IMAGE_TAG" \
  -m unittest -v test_bootstrap test_deployment_checks test_deployment_integration
```

Verificare che tutti i 21 test terminino con `ok` e che il riepilogo finale
riporti `OK`, senza errori o fallimenti.

Controllare nell'elenco i casi di:

- soglie bootstrap inclusive: `R2 >= 0.80` e `RMSE <= 300`, entrambe richieste;
- rifiuto del bootstrap con `champion` già presente;
- rifiuto di errori Registry o di rete, senza interpretarli come assenza di
  `champion`;
- rifiuto del deployment se `champion` cambia o compare durante l'attesa
  dell'approvazione;
- accettazione della versione esatta quando il riferimento è invariato;
- promozione verificata di `champion`, senza modificare `baseline` e `candidate`.

Non cancellare o modificare gli alias del Registry reale per eseguire
questo scenario.

## Verificare il riferimento champion fissato dallo snapshot

In un terminale WSL, mantenere `GIT_REPOSITORY` e `IMAGE_TAG` impostati come
nel capitolo precedente ed eseguire:

```bash
docker run --rm --network none \
  --mount "type=bind,src=$GIT_REPOSITORY,dst=/workspace,readonly" \
  --workdir /tmp \
  -e PYTHONPATH=/workspace/training/src:/workspace/training/tests \
  --entrypoint python \
  "$IMAGE_TAG" \
  -m unittest -v \
  test_evaluation.EvaluationTests.test_snapshot_is_resolved_once_and_alias_change_does_not_change_gate
```

Verificare che il test termini con `ok` e che il riepilogo riporti
`Ran 1 test` e `OK`, senza errori o fallimenti.

Atteso: dopo il cambio simulato di `champion` dalla versione `1` alla `2`,
il confronto e il report usano ancora la versione `1` fissata nello
snapshot, senza rileggere l'alias.

Non modificare l'alias `champion` nel Registry reale.

## Eseguire un rollback manuale

### Selezionare il rilascio da ripristinare

Verificare che non ci siano workflow in esecuzione o deployment in attesa di
approvazione. Non avviare workflow o altri deploy durante il rollback.

In MLflow, annotare le versioni correnti di `champion`, `candidate` e
`baseline`. Eseguire i controlli dell'InferenceService e del ServingRuntime
indicati in [Verificare il deployment](#verificare-il-deployment) e annotare
versione, run sorgente, URI S3 e immagine runtime correnti.

In **Actions**, scegliere una precedente esecuzione con deployment riuscito
di una versione diversa da quella attualmente servita. Scaricare ed estrarre
l'artifact `deployment-<run ID GitHub>-<run attempt>` fuori dal repository.

Verificare i sei file come in
[Verificare gli artefatti](#verificare-gli-artefatti). In MLflow, verificare che
la versione da ripristinare sia ancora `READY` e che run sorgente e URI del
modello coincidano con quelli dell'artifact.

### Caricare l'immagine del rilascio precedente

In un terminale WSL, impostare la directory estratta e la versione da
ripristinare. Copiare in `IMAGE_TAG` il valore `images.serving.image` di
`deployment-result.json`:

```bash
export ROLLBACK_DIR="/mnt/c/<directory-artefatto-estratto>"
export ROLLBACK_VERSION="<versione da ripristinare>"
export IMAGE_TAG="<images.serving.image>"
export MANIFEST="$ROLLBACK_DIR/gp-mof-capstone-model-v$ROLLBACK_VERSION.yaml"

docker image inspect "$IMAGE_TAG" --format '{{.Id}}'
```

Verificare che l'image ID sia uguale a `images.serving.image_id` di
`deployment-result.json`. Se l'immagine non è disponibile o non coincide,
interrompere lo scenario; non ricostruirla con lo stesso tag.

Caricare l'immagine in KinD e controllarne l'identità nel nodo:

```bash
kind load docker-image "$IMAGE_TAG" --name mlops-test

docker exec mlops-test-control-plane crictl inspecti "$IMAGE_TAG" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["status"]["id"])'
```

Atteso: il valore di
`kind_image.node_image_ids.mlops-test-control-plane` nell'artifact.

### Riapplicare runtime e modello

Verificare che il Secret S3 sia aggiornato seguendo
[Applicare il Secret S3](../serving/serving-kserve-model.md#applicare-il-secret-s3).

Validare entrambi i manifest prima di applicarli:

```bash
kubectl --context kind-mlops-test apply --dry-run=server \
  -f "$ROLLBACK_DIR/20-serving-runtime.yaml"
kubectl --context kind-mlops-test apply --dry-run=server -f "$MANIFEST"
```

Procedere soltanto se entrambe le validazioni terminano senza errori:

```bash
kubectl --context kind-mlops-test apply \
  -f "$ROLLBACK_DIR/20-serving-runtime.yaml"
kubectl --context kind-mlops-test apply -f "$MANIFEST"
```

Attendere che il Deployment recepisca la versione e l'immagine del rollback:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  wait deployment/gp-mof-capstone-model-predictor \
  "--for=jsonpath={.spec.template.metadata.annotations.mlops\.gp-mof\.dev/resolved-version}=$ROLLBACK_VERSION" \
  --timeout=60s

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  wait deployment/gp-mof-capstone-model-predictor \
  "--for=jsonpath={.spec.template.spec.containers[?(@.name==\"kserve-container\")].image}=$IMAGE_TAG" \
  --timeout=60s

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  rollout status deployment/gp-mof-capstone-model-predictor --timeout=60s

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  wait --for=condition=Ready inferenceservice/gp-mof-capstone-model --timeout=60s
```

Atteso: i due controlli JSONPath e il controllo `Ready` terminano con
`condition met`; il rollout termina con `successfully rolled out`. Se un
comando fallisce o scade il timeout, interrompere lo scenario senza modificare
`champion`.

Verificare versione, run sorgente, URI S3 e
immagine runtime con i comandi di
[Verificare il deployment](#verificare-il-deployment). Atteso: i riferimenti
del rilascio selezionato, non quelli annotati prima del rollback.

Eseguire il controllo dell'immagine del pod indicato in
[Applicare l'InferenceService e attendere](../serving/serving-kserve-model.md#applicare-linferenceservice-e-attendere).
Verificare che il pod pronto usi l'immagine `IMAGE_TAG` del rollback.

Controllare anche versione e run sorgente dei pod:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get pods -l app=isvc.gp-mof-capstone-model-predictor \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.mlops\.gp-mof\.dev/resolved-version}{"\t"}{.metadata.annotations.mlops\.gp-mof\.dev/source-run-id}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
```

Prima di provare l'inferenza, verificare che rimanga un solo pod predictor
pronto, con la versione e il run sorgente del rilascio ripristinato.

Eseguire la prova di
[Verificare il modello e l'inferenza V2](../serving/serving-kserve-model.md#verificare-il-modello-e-linferenza-v2).
Usare l'esempio recuperato dal modello ripristinato; verificare risposte HTTP
`200`, nome `gp-mof-capstone-model` e una predizione numerica finita per ogni
riga dell'esempio. Non confrontare le predizioni con i valori dell'esempio
V1 riportato nella procedura di serving. Terminare il port-forward al
predictor al termine della prova.

In caso di errore o timeout, interrompere lo scenario senza modificare
`champion`. Controllare servizio e Registry prima di riprovare.

### Riallineare champion al modello ripristinato

Solo dopo readiness e inferenza riuscite, verificare in MLflow che
`champion` punti ancora alla versione annotata prima del rollback e
aggiornarlo alla versione ripristinata:

```bash
curl --fail --silent --show-error \
  -X POST \
  -H 'Host: localhost:5000' \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"gp-mof-capstone-model\",\"alias\":\"champion\",\"version\":\"$ROLLBACK_VERSION\"}" \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/alias'
```

Atteso: risposta `{}`. Rileggere gli alias:

```bash
curl --fail --silent --show-error \
  -H 'Host: localhost:5000' \
  'http://127.0.0.1:5000/api/2.0/mlflow/registered-models/get?name=gp-mof-capstone-model'
```

Verificare che `champion` punti a `ROLLBACK_VERSION` e che `candidate` e
`baseline` siano invariati. In caso di errore della chiamata di aggiornamento,
rileggere comunque gli alias prima di riprovare.

Confermare che l'InferenceService serva ancora la versione ripristinata.
