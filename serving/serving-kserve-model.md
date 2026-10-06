# Serving del modello con KServe

Questa procedura parte dal repository corrente e dalla fase di
[risoluzione del deployment](../deployment/deployment-resolution-kserve-manifest.md)
completata. Prepara il runtime MLServer dedicato, configura l'accesso S3 su
SeaweedFS e applica il manifest KServe del modello `gp-mof-capstone-model`,
fino a una richiesta di inferenza HTTP con protocollo V2.

Il test Iris nel namespace `mlops-iris-inference` non viene modificato.

## Prerequisiti

- WSL2 con Docker Desktop integrato e Docker Compose funzionante;
- `kind`, `kubectl`, `rsync`, `sed`, `find`, `curl` e `python3` disponibili in WSL;
- accesso `sudo` e directory `/opt/mlops-local-poc/` posseduta dall'utente WSL;
- cluster `mlops-test`, contesto `kind-mlops-test`, KServe in modalità Standard
  e infrastruttura Gateway API/Envoy già installati
  ([procedura KServe](../kserve/kserve-kind-wsl-setup.md));
- stack MLflow/PostgreSQL/SeaweedFS predisposto in
  `/opt/mlops-local-poc/mlflow/`, con credenziali locali valide nel file `.env`;
- modello registrato `gp-mof-capstone-model`, alias `champion` e artifact S3
  disponibili;
- componente `deployment/` già operativo, con manifest e lock di provenance
  prodotti dalla fase precedente;
- repository completo delle directory `serving/` e `deployment/`.

Tutti i comandi si eseguono in un terminale **Bash in WSL**. Gli esempi assumono `champion` risolto alla Version 1; con un'altra versione usare manifest e lock corrispondenti.

## Preparare la directory operativa e sincronizzare

Impostare il percorso del repository Windows visto da WSL:

```bash
export REPO="/mnt/c/<percorso-repository>/mlops-local-poc"
ls -d "${REPO}/serving" "${REPO}/deployment"

sudo install -d -o "$USER" -g "$(id -gn)" -m 0755 \
  /opt/mlops-local-poc/serving

bash "${REPO}/deployment/scripts/sync-to-wsl.sh"
bash "${REPO}/serving/scripts/sync-to-wsl.sh"
```

Il comando `ls -d` deve stampare i due percorsi, senza errori.

```bash
find /opt/mlops-local-poc/serving -maxdepth 2 -type f | sort
```

Devono essere presenti almeno `Dockerfile`, `requirements.txt`,
`config/storage-secret.env`, i manifest `00`, `10` e `20` e gli script
`build-image.sh` e `apply-storage-secret.sh`.

## Verificare stack e contesto Kubernetes

Avviare lo stack MLflow se non è già attivo:

```bash
cd /opt/mlops-local-poc/mlflow
docker compose --env-file .env up -d
docker compose --env-file .env ps

kubectl config use-context kind-mlops-test
kubectl --context kind-mlops-test get nodes
kubectl --context kind-mlops-test get crd \
  inferenceservices.serving.kserve.io \
  servingruntimes.serving.kserve.io
```

Devono risultare attivi `mlops-postgres`, `mlops-seaweedfs` e `mlops-mlflow`;
il nodo deve essere `Ready` e le due CRD presenti.

## Costruire e caricare l'immagine MLServer

Costruire l'immagine del runtime:

```bash
cd /opt/mlops-local-poc/serving
./scripts/build-image.sh

docker image inspect gp-mof-capstone-mlserver:dev \
  --format 'image_id={{.Id}} command={{json .Config.Cmd}}'
```

Il comando di avvio deve essere `["mlserver","start","/mnt/models"]`.

Caricare l'immagine nel nodo KinD:

```bash
kind load docker-image gp-mof-capstone-mlserver:dev --name mlops-test

docker exec mlops-test-control-plane crictl images \
  | grep gp-mof-capstone-mlserver
```

L'immagine deve comparire nel nodo con tag `dev`. Ripetere il caricamento dopo
una nuova build o dopo la ricreazione del cluster.

## Creare il namespace

```bash
kubectl --context kind-mlops-test apply \
  -f /opt/mlops-local-poc/serving/manifests/00-namespace.yaml
```

## Collegare KinD alla rete MLflow

Elencare le reti del nodo:

```bash
docker inspect mlops-test-control-plane \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
```

Se manca `mlops-local-network`, collegarlo:

```bash
docker network connect mlops-local-network mlops-test-control-plane
```

Ripetere l'ispezione: ora l'elenco deve includere `mlops-local-network`. Il
collegamento va ripetuto dopo aver ricreato il cluster.

## Applicare il Secret S3

```bash
/opt/mlops-local-poc/serving/scripts/apply-storage-secret.sh
```

Lo script legge le credenziali da `/opt/mlops-local-poc/mlflow/.env` e risolve
l'IP corrente di SeaweedFS. Rieseguirlo se lo stack viene ricreato o le
credenziali cambiano.

Verificare solo l'endpoint:

```bash
docker inspect mlops-seaweedfs \
  --format '{{with index .NetworkSettings.Networks "mlops-local-network"}}{{.IPAddress}}{{end}}'

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get secret gp-mof-capstone-model-s3-credentials \
  -o jsonpath='{.metadata.annotations.serving\.kserve\.io/s3-endpoint}{"\n"}'
```

L'annotation deve riportare l'IP appena letto, seguito da `:8333`. Non
stampare il contenuto di `.env` o del Secret.

## Applicare ServiceAccount e runtime

Applicare il ServiceAccount e il `ServingRuntime` (prima validato lato server):

```bash
cd /opt/mlops-local-poc/serving
kubectl --context kind-mlops-test apply -f manifests/10-service-account.yaml

kubectl --context kind-mlops-test apply --dry-run=server \
  -f manifests/20-serving-runtime.yaml
kubectl --context kind-mlops-test apply -f manifests/20-serving-runtime.yaml

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get serviceaccount gp-mof-capstone-model-storage-reader
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get servingruntime gp-mof-capstone-mlserver
```

I due `get` finali devono mostrare le risorse create, senza errori.

## Verificare il manifest di deployment

Usare il manifest e il lock prodotti dalla fase di deployment:

```bash
export MANIFEST=/opt/mlops-local-poc/deployment/rendered/gp-mof-capstone-model-v1.yaml
cat "$MANIFEST"
cat "${MANIFEST%.yaml}.resolution.json"
```

Verificare nel manifest:

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

Il lock deve indicare `requested_alias: champion`, `resolved_version: 1`,
`status: READY` e lo stesso URI S3 del manifest. Model ID e run ID dipendono
dal training eseguito.

Se `runtime` è diverso, il manifest è stato generato con una configurazione
precedente: ricostruire il container del resolver e ripetere la fase di deployment.

Cambiare l'alias MLflow non aggiorna il servizio: per servire un'altra versione
renderizzare un nuovo manifest e applicarlo.

## Applicare l'InferenceService e attendere

```bash
kubectl --context kind-mlops-test apply \
  --dry-run=server -f "$MANIFEST"
kubectl --context kind-mlops-test apply -f "$MANIFEST"
```

Solo se il predictor esiste già (immagine ricostruita o Secret aggiornato):

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  rollout restart deployment/gp-mof-capstone-model-predictor
```

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  rollout status deployment/gp-mof-capstone-model-predictor --timeout=60s

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  wait --for=condition=Ready inferenceservice/gp-mof-capstone-model --timeout=60s

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get deployment,pods,inferenceservice
```

Atteso: `rollout status` termina con `successfully rolled out`, `wait` con
`condition met`; poi Deployment `1/1`, pod `Running` e `1/1`,
`InferenceService` `READY=True`. In caso di timeout vedere la diagnostica.

## Verificare il modello e l'inferenza V2

Nel **terminale del port-forward** (WSL), lasciare attivo un port-forward diretto
al predictor:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  port-forward deployment/gp-mof-capstone-model-predictor 8082:8080
```

Nel **terminale dei comandi** (WSL), separato:

```bash
cd /opt/mlops-local-poc/serving
mkdir -p runtime

curl --fail --silent --show-error --max-time 10 \
  -w '%{http_code}\n' \
  http://127.0.0.1:8082/v2/models/gp-mof-capstone-model/ready

curl --fail --silent --show-error --max-time 10 \
  -w '%{http_code}\n' \
  http://127.0.0.1:8082/v2/models/gp-mof-capstone-model \
  -o runtime/model-metadata.json
./scripts/show-model-metadata.py runtime/model-metadata.json
```

Atteso: `200` stampato da entrambi i comandi e il riepilogo
`gp-mof-capstone-model 14 inputs` seguito dall'elenco dei nomi e dei tipi degli input.

Recuperare dal pod l'esempio salvato con il modello:

```bash
POD=$(kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get pods -l app=isvc.gp-mof-capstone-model-predictor \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  exec "$POD" -c kserve-container -- cat /mnt/models/serving_input_example.json \
  > runtime/serving-input-example.json
```

Convertire l'esempio in input V2:

```bash
./scripts/build-infer-request.py \
  --metadata runtime/model-metadata.json \
  --example runtime/serving-input-example.json \
  --output runtime/infer-request.json

curl --fail --silent --show-error --max-time 15 \
  -H 'Content-Type: application/json' \
  --data-binary @runtime/infer-request.json \
  -w '%{http_code}\n' \
  http://127.0.0.1:8082/v2/models/gp-mof-capstone-model/infer \
  -o runtime/infer-response.json

python3 -m json.tool runtime/infer-response.json
```

Atteso: `200` stampato, `model_name: gp-mof-capstone-model` e un output numerico
per ogni riga dell'esempio. Nel lab, con la V1: `898.69`, `901.69` e
`1278.29`.

I file JSON di test restano in `/opt/mlops-local-poc/serving/runtime/`, fuori dal
repository.

## Verificare anche il routing Envoy

Identificare il Service Envoy:

```bash
kubectl --context kind-mlops-test get svc -A \
  -l gateway.envoyproxy.io/owning-gateway-name=kserve-ingress-gateway

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get httproute \
  -o custom-columns='NAME:.metadata.name,HOSTNAMES:.spec.hostnames'
```

Nel terminale del port-forward, interrompere con `Ctrl+C` il port-forward al
predictor e avviare quello verso Envoy, con namespace e nome del Service trovati
sopra:

```bash
kubectl --context kind-mlops-test \
  -n <ENVOY-SERVICE-NAMESPACE> \
  port-forward service/<ENVOY-SERVICE-NAME> 8080:80
```

Nel terminale dei comandi, con l'hostname della route `gp-mof-capstone-model` (non quella `…-predictor`),
nella forma `<nome>-<namespace>.example.com`, come mostrato dall'elenco sopra:

```bash
cd /opt/mlops-local-poc/serving
HOST="gp-mof-capstone-model-gp-mof-capstone-serving.example.com"

curl --fail --silent --show-error --max-time 10 \
  -H "Host: $HOST" -w '%{http_code}\n' \
  http://127.0.0.1:8080/v2/models/gp-mof-capstone-model/ready

curl --fail --silent --show-error --max-time 15 \
  -H "Host: $HOST" -H 'Content-Type: application/json' \
  --data-binary @runtime/infer-request.json \
  -w '%{http_code}\n' \
  http://127.0.0.1:8080/v2/models/gp-mof-capstone-model/infer \
  -o runtime/infer-response-envoy.json

python3 -m json.tool runtime/infer-response-envoy.json
```

Atteso: `200` dopo ciascuna risposta e le stesse predizioni della chiamata diretta. In KinD
`EXTERNAL-IP=<pending>` e `Programmed=False` sono previsti e non bloccanti.

Terminare i port-forward con `Ctrl+C` al termine delle verifiche.

## Diagnostica essenziale

In caso di mancata readiness:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  describe inferenceservice gp-mof-capstone-model

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get pods -o wide

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  get events --sort-by=.lastTimestamp
```

Individuare il pod che fallisce e sostituire `<POD>`:

```bash
kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  logs <POD> -c storage-initializer --tail=60

kubectl --context kind-mlops-test -n gp-mof-capstone-serving \
  logs <POD> -c kserve-container --tail=60
```

- `Unable to locate credentials`: verificare Secret, namespace e annotation
  `serving.kserve.io/secretName` sull'`InferenceService`.
- Timeout verso S3: verificare collegamento del nodo alla rete Docker e IP
  corrente di SeaweedFS; riapplicare il Secret e riavviare il predictor.
- `ErrImagePull`/`ImagePullBackOff`: verificare build e caricamento in KinD.
- Errori MLServer all'avvio o al caricamento del modello: verificare di aver
  sincronizzato, ricostruito e caricato l'immagine e applicato il runtime correnti.
- Chiamata diretta riuscita ma Envoy restituisce 404/503: verificare Service
  del port-forward, hostname e condizioni della `HTTPRoute`.

## Stato finale

- Namespace `gp-mof-capstone-serving`, Secret S3 e ServiceAccount presenti.
- Nodo KinD collegato a `mlops-local-network`.
- Immagine `gp-mof-capstone-mlserver:dev` caricata nel cluster e runtime
  namespaced `gp-mof-capstone-mlserver` applicato.
- Manifest e lock coerenti con la versione risolta dal Model Registry.
- Predictor `1/1` e `InferenceService` `READY=True`.
- Modello pronto e richiesta V2 conclusa con HTTP 200 e predizioni numeriche.
- Test Iris e runtime globale KServe invariati.
