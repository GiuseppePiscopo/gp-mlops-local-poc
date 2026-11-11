# Deployment gp-mof-capstone: risoluzione MLflow e rendering manifest KServe

Questa procedura seleziona il modello registrato `gp-mof-capstone-model` tramite alias MLflow o versione esplicita, verifica che la versione sia pronta, traduce l’URI logico degli artefatti nel corrispondente URI S3 SeaweedFS e genera un manifest KServe `InferenceService` con il relativo lock JSON di provenance.

La procedura **non applica** il manifest al cluster Kubernetes e non avvia il serving del modello. Per collegare KinD alla rete SeaweedFS, applicare il Secret S3 e avviare l'`InferenceService`, seguire la [procedura di serving](../serving/serving-kserve-model.md).

## Prerequisiti

Il repository deve già contenere tutti i file statici del componente `deployment/`.

Devono inoltre essere già disponibili:

- WSL2 con Docker Desktop integrato;
- Docker Engine e Docker Compose funzionanti;
- cluster KinD con KServe installato;
- stack MLflow, PostgreSQL e SeaweedFS avviabile da `/opt/mlops-local-poc/mlflow/`;
- modello MLflow registrato con nome `gp-mof-capstone-model`;
- alias MLflow `champion` e/o `candidate`;
- rete Docker `mlops-local-network`, creata dallo stack MLflow;
- directory `/opt/mlops-local-poc/` posseduta dall’utente WSL corrente.
- Git e `python3` >= 3.11 in WSL; repository sorgente pulito, completo di
  `ci-cd/scripts/prepare_image.py`.
- Git LFS installato anche in WSL; repository già configurato per LFS e file LFS già scaricati.

Il repository Windows è la sorgente autorevole dei file versionati. Il workspace operativo WSL è:

```text
/opt/mlops-local-poc/deployment/
```

Impostare la variabile che identifica la directory `deployment/` nel repository Windows:

```bash
export SOURCE_DEPLOYMENT="/mnt/c/<percorso-repository>/mlops-local-poc/deployment"
```

Verificare che il percorso esista:

```bash
test -d "${SOURCE_DEPLOYMENT}"

echo "${SOURCE_DEPLOYMENT}"
```

L’output deve mostrare il percorso reale della directory `deployment/` nel repository.

## Avviare MLflow

Avviare lo stack MLflow se non è già attivo.

```bash
cd /opt/mlops-local-poc/mlflow

docker compose --env-file .env up -d
```

Verificare che tutti i container siano avviati:

```bash
docker compose --env-file .env ps
```

Controllare che risultino attivi almeno:

```text
mlops-postgres
mlops-seaweedfs
mlops-mlflow
```

Il deployment resolver eseguirà il container nella rete Docker `mlops-local-network` e raggiungerà MLflow tramite:

```text
http://mlflow:5000
```

## Sincronizzare il workspace WSL

Sincronizzare i file statici dal repository Windows al workspace WSL. La directory runtime `rendered/`, che contiene manifest e lock generati, resta intatta.

```bash
bash "${SOURCE_DEPLOYMENT}/scripts/sync-to-wsl.sh"
```

Verificare la struttura operativa:

```bash
find /opt/mlops-local-poc/deployment -maxdepth 2 \
  \( -type d -o -type f \) \
  | sort
```

Controllare in particolare la presenza di:

```text
/opt/mlops-local-poc/deployment/Dockerfile
/opt/mlops-local-poc/deployment/config/deployment-target.local.yaml
/opt/mlops-local-poc/deployment/requirements.txt
/opt/mlops-local-poc/deployment/scripts/build-image.sh
/opt/mlops-local-poc/deployment/scripts/render.sh
/opt/mlops-local-poc/deployment/scripts/sync-to-wsl.sh
/opt/mlops-local-poc/deployment/src/resolve_and_render.py
/opt/mlops-local-poc/deployment/templates/inferenceservice.yaml.j2
```

## Costruire l’immagine Docker

Impostare il percorso del repository Git Windows e il tag. Il repository
deve essere pulito, senza modifiche staged, unstaged o file non tracciati.
Esportare le variabili anche nei terminali da cui si eseguiranno i render.

```bash
export GIT_REPOSITORY="/mnt/c/<percorso-repository>/mlops-local-poc"
export DEPLOYMENT_RESOLVER_IMAGE="gp-mof-capstone-deployment-resolver:git-$(git -C "$GIT_REPOSITORY" rev-parse HEAD)"
```

Costruire l’immagine del deployment resolver dal build context WSL.

```bash
cd /opt/mlops-local-poc/deployment

./scripts/build-image.sh
```

L’immagine prodotta è:

```text
gp-mof-capstone-deployment-resolver:git-<SHA completo>
```

Controllare nell'output JSON `image`, `image_id` e `git_commit`.

Verificare l’immagine:

```bash
docker image inspect \
  "$DEPLOYMENT_RESOLVER_IMAGE" \
  --format 'image_id={{.Id}} created={{.Created}}'
```

L’output atteso è simile a:

```text
image_id=sha256:...
```

Per ricostruire lo stesso commit, usare un nuovo suffisso `-rebuild1` in
`DEPLOYMENT_RESOLVER_IMAGE`, sia in build sia nei render.

## Renderizzare il deployment champion

Non cambiare commit tra build e render.

Nella configurazione corrente l’alias è `champion`. Eseguire il renderer per risolverlo in una versione concreta e generare il manifest KServe con il relativo lock JSON di provenance.

```bash
/opt/mlops-local-poc/deployment/scripts/render.sh
```

L’output JSON deve contenere valori simili ai seguenti:

```json
{
  "rendered_manifest_path": "/output/gp-mof-capstone-model-v1.yaml",
  "resolution": {
    "registered_model": "gp-mof-capstone-model",
    "requested_alias": "champion",
    "requested_version": null,
    "resolved_version": "1",
    "resolved_storage_uri": "s3://mlflow-artifacts/2/models/<model-id>/artifacts",
    "status": "READY"
  },
  "resolution_lock_path": "/output/gp-mof-capstone-model-v1.resolution.json"
}
```

Il manifest KServe dichiara il runtime dedicato `gp-mof-capstone-mlserver`, il formato modello `mlflow` e il protocollo V2, come configurati nel repository corrente.

In questa fase il runtime viene soltanto referenziato: non occorre ancora costruire la sua immagine o applicare il `ServingRuntime` al cluster. Queste operazioni, la verifica della compatibilita con il modello e la prova di inferenza appartengono alla successiva [procedura di serving](../serving/serving-kserve-model.md). La validazione del manifest non verifica che il runtime referenziato sia presente o funzionante.

## Ispezionare gli output

Elencare gli artifact generati:

```bash
ls -1 /opt/mlops-local-poc/deployment/rendered/
```

Per `@champion` risolto alla V1, devono essere presenti:

```text
gp-mof-capstone-model-v1.resolution.json
gp-mof-capstone-model-v1.yaml
```

Visualizzare il manifest renderizzato:

```bash
cat /opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v1.yaml
```

Controllare in particolare:

```yaml
metadata:
  name: "gp-mof-capstone-model"
  namespace: "gp-mof-capstone-serving"
  annotations:
    serving.kserve.io/secretName: "gp-mof-capstone-model-s3-credentials"

spec:
  predictor:
    serviceAccountName: "gp-mof-capstone-model-storage-reader"
    model:
      runtime: "gp-mof-capstone-mlserver"
      modelFormat:
        name: "mlflow"
      protocolVersion: "v2"
      storageUri: "s3://mlflow-artifacts/2/models/<model-id>/artifacts"
```

Il nome del Secret usato dallo storage initializer è configurato in
`kserve.storage_secret_name`. L'annotation va sul metadata dell'`InferenceService`;
l'annotation sul solo ServiceAccount non seleziona il Secret per lo storage
initializer.

Visualizzare il lock JSON:

```bash
cat /opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v1.resolution.json
```

Il lock deve contenere:

- modello registrato;
- alias richiesto (`requested_alias`) e versione richiesta (`requested_version`),
  con il selettore non usato uguale a `null`;
- versione concreta risolta;
- run ID MLflow;
- URI sorgente della Model Version;
- URI di download MLflow;
- `storageUri` S3 risolto;
- stato `READY`;
- nome del manifest associato.

Il lock è una fotografia immutabile della decisione di deployment effettuata in quel momento.

## Validare il manifest Kubernetes

Validare il manifest contro le API Kubernetes senza creare o modificare risorse nel cluster.

```bash
kubectl apply \
  --dry-run=client \
  --filename /opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v1.yaml
```

L’output atteso è:

```text
inferenceservice.serving.kserve.io/gp-mof-capstone-model created (dry run)
```

Questo controllo valida la struttura del manifest e la disponibilità della CRD `InferenceService`, ma non verifica ancora:

- esistenza del namespace `gp-mof-capstone-serving`;
- esistenza del ServiceAccount `gp-mof-capstone-model-storage-reader`;
- accesso del cluster a SeaweedFS;
- credenziali S3;
- download del modello;
- avvio di MLServer;
- inferenza HTTP.

Rimandare queste verifiche alla procedura operativa di serving KServe.

## Renderizzare una versione candidate

Per renderizzare esplicitamente l’alias `candidate`, senza cambiare il file YAML di configurazione, usare l’override CLI:

```bash
/opt/mlops-local-poc/deployment/scripts/render.sh \
  --alias candidate
```

Il renderer produce un manifesto distinto, per esempio:

```text
/opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v2.yaml
```

e il lock associato:

```text
/opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v2.resolution.json
```

Verificare i file prodotti:

```bash
ls -1 /opt/mlops-local-poc/deployment/rendered/
```

Controllare che manifest e lock della V2 siano coerenti:

```bash
grep -E \
  'requested-alias|resolved-version|resolved-storage-uri|storageUri' \
  /opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v2.yaml

cat /opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v2.resolution.json
```

L’alias `candidate` deve risultare nel manifest e nel lock, insieme alla versione concreta e allo `storageUri` della V2.

## Renderizzare una versione esatta

Aprire **Models > gp-mof-capstone-model** in MLflow e annotare il numero della
versione appena registrata. Impostarlo in WSL:

```bash
export MODEL_VERSION="<numero-versione>"
```

Renderizzare quella versione senza risolvere un alias:

```bash
/opt/mlops-local-poc/deployment/scripts/render.sh \
  --registered-model gp-mof-capstone-model \
  --version "${MODEL_VERSION}"
```

Non aggiungere `--alias` al comando.

Ispezionare manifest e lock:

```bash
grep -E \
  'requested-version|resolved-version|source-run-id|resolved-storage-uri|storageUri' \
  "/opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v${MODEL_VERSION}.yaml"

cat "/opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v${MODEL_VERSION}.resolution.json"
```

Verificare:

- nel lock, `requested_alias` uguale a `null`, `requested_version` e
  `resolved_version` uguali a `MODEL_VERSION`, stato `READY`;
- run sorgente e URI della Model Version uguali a quelli della versione
  selezionata in MLflow;
- nel manifest, annotation `mlops.gp-mof.dev/requested-version` uguale a
  `MODEL_VERSION` e assenza di `mlops.gp-mof.dev/requested-alias`;
- `storageUri` del manifest uguale a `resolved_storage_uri` nel lock.

Una versione inesistente, non pronta o non recuperabile interrompe il render,
senza ripiegare su un alias e senza scrivere nuovi manifest o lock.

## Verificare il fail-fast

Verificare che un alias MLflow inesistente non produca manifest o lock aggiuntivi.

```bash
/opt/mlops-local-poc/deployment/scripts/render.sh \
  --alias alias-inesistente

echo "Exit code: $?"
```

L’output atteso contiene un errore di risoluzione MLflow e un exit code diverso da zero:

```text
Deployment resolution failed: ...
Exit code: 1
```

Verificare che non siano comparsi file inattesi:

```bash
ls -1 /opt/mlops-local-poc/deployment/rendered/
```

Il resolver interrompe l’esecuzione anche se la Model Version risolta non è in stato `READY`; in quel caso non produce manifest né lock.

## Sequenza operativa ordinaria

Dopo una modifica ai file nel repository Windows:

```bash
bash "${SOURCE_DEPLOYMENT}/scripts/sync-to-wsl.sh"

cd /opt/mlops-local-poc/deployment

./scripts/build-image.sh

./scripts/render.sh
```

Per renderizzare un alias diverso da quello configurato:

```bash
./scripts/render.sh \
  --alias candidate
```

Gli output persistenti sono sempre disponibili in:

```text
/opt/mlops-local-poc/deployment/rendered/
```

## Stato finale della fase

Al termine della procedura risultano verificati:

- immagine Docker `gp-mof-capstone-deployment-resolver:git-<SHA>` costruita da
  snapshot committato, con image ID locale e label di provenance verificabili;
- alias MLflow risolto in una Model Version in stato `READY`;
- URI degli artifact tradotto nell'URI S3 SeaweedFS configurato;
- manifest KServe `InferenceService` e lock JSON di provenance generati;
- manifest validato con `kubectl apply --dry-run=client`;
- errore su alias inesistente gestito senza produrre manifest o lock aggiuntivi.

L'applicazione del manifest al cluster e la verifica del serving del modello non fanno parte di questa procedura.
