# KServe su KinD/WSL: ambiente Standard mode pronto per le inferenze

Obiettivo: installare su un cluster KinD in WSL un ambiente KServe v0.20.0 con cert-manager, Kubernetes Gateway API ed Envoy Gateway, pronto per eseguire inferenze predittive tramite `InferenceService`.

### Verificare il cluster e gli strumenti

Verificare context, versione del server Kubernetes e disponibilità di Helm prima dell'installazione.

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl version
helm version
```

### Installare cert-manager

Installare cert-manager e le sue CRD, richiesti dai webhook di KServe.

```bash
helm upgrade --install cert-manager \
  oci://quay.io/jetstack/charts/cert-manager \
  --version v1.21.2 \
  --namespace cert-manager \
  --create-namespace \
  --set crds.enabled=true \
  --wait \
  --timeout 5m

kubectl wait \
  --for=condition=Available \
  deployment \
  --all \
  -n cert-manager \
  --timeout=5m
```

### Installare le CRD Gateway API

Installare il bundle Gateway API Standard compatibile con KServe.

```bash
kubectl apply --server-side \
  -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml

kubectl get crd \
  gatewayclasses.gateway.networking.k8s.io \
  gateways.gateway.networking.k8s.io \
  httproutes.gateway.networking.k8s.io \
  referencegrants.gateway.networking.k8s.io
```

### Installare le CRD di Envoy Gateway

Installare solo le CRD proprietarie di Envoy Gateway; le Gateway API CRD sono già installate e non devono essere sostituite dal chart Envoy.

```bash
helm template eg \
  oci://docker.io/envoyproxy/gateway-crds-helm \
  --version v1.5.9 \
  --set crds.gatewayAPI.enabled=false \
  --set crds.envoyGateway.enabled=true \
  | kubectl apply --server-side -f -

kubectl get crd | grep -E 'gateway.envoyproxy.io|gateway.envoyproxy'
```

### Installare Envoy Gateway

Installare il controller Envoy Gateway senza reinstallare CRD, usando le CRD già gestite separatamente.

```bash
helm upgrade --install eg \
  oci://docker.io/envoyproxy/gateway-helm \
  --version v1.5.9 \
  --namespace envoy-gateway-system \
  --create-namespace \
  --skip-crds \
  --wait \
  --timeout 5m

kubectl wait \
  --for=condition=Available \
  deployment/envoy-gateway \
  -n envoy-gateway-system \
  --timeout=5m
```

### Creare GatewayClass e Gateway KServe

I comandi seguenti assumono che ci si trovi nella directory `kserve` del progetto.
Spostarsi in quella directory o copiarla nella propria directory home (ad esempio per WSL Ubuntu).

Creare il Gateway HTTP che KServe userà per associare le proprie `HTTPRoute`.

```bash
kubectl create namespace kserve \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

kubectl apply -f ./config/kserve-gateway.yaml

kubectl get gatewayclass
kubectl get gateway -n kserve
kubectl get pods,svc -n envoy-gateway-system
```

In KinD il Service del proxy Envoy è normalmente `LoadBalancer` con `EXTERNAL-IP` uguale a `<pending>`. Il Gateway può quindi avere `Programmed=False` con motivo `AddressNotAssigned`, pur avendo listener `Accepted=True`, `ResolvedRefs=True` e `Programmed=True`. Non è bloccante per i test locali tramite port-forward.

### Installare le CRD KServe

Installare le CRD KServe prima del controller.

```bash
helm upgrade --install kserve-crd \
  oci://ghcr.io/kserve/charts/kserve-crd \
  --version v0.20.0 \
  --namespace kserve \
  --create-namespace \
  --wait \
  --timeout 5m

kubectl api-resources --api-group=serving.kserve.io
```

### Installare KServe in Standard mode

Installare il controller KServe, abilitare Gateway API, usare il Gateway già creato e disabilitare permanentemente la creazione di `VirtualService` Istio.

```bash
helm upgrade --install kserve \
  oci://ghcr.io/kserve/charts/kserve-resources \
  --version v0.20.0 \
  --namespace kserve \
  --create-namespace \
  --set kserve.controller.deploymentMode=Standard \
  --set kserve.controller.gateway.ingressGateway.enableGatewayApi=true \
  --set kserve.controller.gateway.ingressGateway.createGateway=false \
  --set kserve.controller.gateway.ingressGateway.kserveGateway=kserve/kserve-ingress-gateway \
  --set kserve.controller.gateway.ingressGateway.disableIstioVirtualHost=true \
  --wait \
  --timeout 5m

kubectl wait \
  --for=condition=Available \
  deployment \
  --all \
  -n kserve \
  --timeout=5m
```

### Verificare la configurazione KServe

Verificare che le release Helm KServe siano installate correttamente.

```bash
helm list -n kserve
```

L’output deve mostrare entrambe le release in stato `deployed`:

```text
kserve-crd
kserve
```

Verificare che il controller KServe sia disponibile.

```bash
kubectl get deployment,pod -n kserve
```

L’output deve mostrare il deployment `kserve-controller-manager` con `AVAILABLE` uguale a `1` e il relativo pod in stato `Running`.

Verificare i valori effettivi nel ConfigMap `inferenceservice-config`.

```bash
kubectl get configmap inferenceservice-config \
  -n kserve \
  -o jsonpath='{.data.deploy}{"\n"}{.data.ingress}{"\n"}'
```

Nel primo blocco JSON, corrispondente alla configurazione di deploy, verificare:

```json
"defaultDeploymentMode": "Standard"
```

Nel secondo blocco JSON, corrispondente alla configurazione ingress, verificare:

```json
"enableGatewayApi": true
"kserveIngressGateway": "kserve/kserve-ingress-gateway"
"disableIstioVirtualHost": true
```

Il valore `disableIstioVirtualHost: true` è necessario in questa installazione, perché il routing usa Gateway API e Envoy Gateway, non Istio. Impedisce a KServe di tentare la creazione di risorse Istio `VirtualService` e di generare l’evento `VirtualServiceCRDNotFound`.

### Installare i runtime KServe

Installare i `ClusterServingRuntime` compatibili con KServe v0.20.0, incluso `kserve-sklearnserver` per modelli scikit-learn.

```bash
helm upgrade --install kserve-runtime-configs \
  oci://ghcr.io/kserve/charts/kserve-runtime-configs \
  --version v0.20.0 \
  --namespace kserve \
  --create-namespace \
  --wait \
  --timeout 5m

kubectl get clusterservingruntimes
kubectl get clusterservingruntime kserve-sklearnserver
```

### Esporre localmente il Gateway Envoy

Individuare il Service Envoy generato dal Gateway e inoltrarlo su una porta locale libera.

```bash
kubectl get svc -n envoy-gateway-system

kubectl port-forward \
  -n envoy-gateway-system \
  svc/envoy-kserve-kserve-ingress-gateway-<SUFFIX> \
  8080:80
```

Sostituire `<SUFFIX>` con il suffisso effettivo del Service, ad esempio `deaaa49b`. Il mapping `8080:80` espone il Gateway Kubernetes sulla porta locale `http://localhost:8080` senza occupare la porta 80 dell'host.

### Verificare l'ambiente pronto alle inferenze

Verificare che cert-manager, Envoy Gateway e KServe siano disponibili prima di applicare il primo `InferenceService`.

```bash
kubectl get pods -n cert-manager
```

Verificare che i pod `cert-manager`, `cert-manager-cainjector` e `cert-manager-webhook` siano in stato `Running`.

```bash
kubectl get pods,svc -n envoy-gateway-system
```

Verificare che siano presenti:

```text
envoy-gateway-...
envoy-kserve-kserve-ingress-gateway-...
```

Entrambi i pod devono essere in stato `Running`. Deve inoltre essere presente il Service del proxy Envoy:

```text
envoy-kserve-kserve-ingress-gateway-<SUFFIX>
```

Il Service può avere `TYPE=LoadBalancer` e `EXTERNAL-IP=<pending>` in KinD; è previsto e non blocca i test locali tramite `kubectl port-forward`.

```bash
kubectl get gatewayclass
kubectl get gateway -n kserve
```

Verificare che la `GatewayClass` `envoy` sia `ACCEPTED=True` e che esista il Gateway `kserve-ingress-gateway` nel namespace `kserve`.

```bash
kubectl get deployment,pod -n kserve
kubectl get clusterservingruntimes
```

Verificare che il deployment e il pod `kserve-controller-manager` siano disponibili e che esista il runtime richiesto dal primo modello, ad esempio:

```text
kserve-sklearnserver
```

A questo punto l’ambiente è pronto per applicare il primo `InferenceService`.
