# CI/CD GitHub Actions con runner WSL

Questa procedura configura un runner GitHub Actions nel laboratorio WSL con
Docker e KinD, verifica checkout e build delle immagini ed esegue training
ed evaluation del modello `gp-mof-capstone-model` con MLflow.

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

Nel repository locale, verificare i remote:

```bash
git remote -v
```

Se manca `github`, aggiungerlo:

```bash
git remote add github https://github.com/GiuseppePiscopo/gp-mlops-local-poc.git
```

Verificare la destinazione dei push:

```bash
git remote get-url --push github
git remote get-url --push origin
```

Atteso: `github` punta a `https://github.com/GiuseppePiscopo/gp-mlops-local-poc.git`;
`origin` mantiene l'URL Bitbucket esistente.

Per inviare modifiche a GitHub, specificare sempre il remote:

```bash
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

```bash
git push github main
```

Con il runner attivo, aprire **Actions > Verify runner > Run workflow**
([verify-runner.yml](../.github/workflows/verify-runner.yml)),
selezionare `main` e avviare l'esecuzione.

### Verificare l'esecuzione su GitHub

Aprire l'esecuzione e verificare che il job `verify-checkout` e tutti i suoi
step siano completati con successo.

Nel **Summary**, controllare la sezione **Linux checkout and image**:

| Campo    | Valore atteso                                                            |
| -------- | ------------------------------------------------------------------------ |
| Commit   | SHA completo del commit selezionato per l'esecuzione                     |
| Image    | `gp-mof-capstone-training:git-<Commit>`, con lo SHA del campo precedente |
| Image ID | `sha256:` seguito da 64 caratteri esadecimali                            |
| Reused   | `False` per una nuova immagine; `True` se era già presente e verificata  |

### Verificare il checkout in WSL

Cliccare sul job `verify-checkout` per aprire il dettaglio coi log, aprire lo step
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

Aprire l'esecuzione e verificare che il job **Train and evaluate** e tutti
i suoi step siano completati con successo.

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

Aprire **Models > gp-mof-capstone-model** in MLflow e selezionare la versione
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

## Approvare l'esecuzione production

Nel dettaglio dell'esecuzione GitHub, verificare che **Train and evaluate**
sia completato con successo e **Deploy (approval only)** sia in attesa di
review per l'environment `production`.

Aprire **Review deployments**, selezionare `production` e premere
**Approve and deploy**.

Verificare il successo del job **Deploy (approval only)** e, nel Summary,
la sezione **Production approval**:

- modello e versione uguali alla sezione **Model registration**;
- run sorgente, URI del modello e commit uguali alla sezione **Training result**;
- indicazione che il job non ha modificato risorse del cluster o alias.
