#!/bin/bash
echo '=== Helm ==='
helm list -A

echo
echo '=== cert-manager ==='
kubectl get deployment,pod -n cert-manager

echo
echo '=== Envoy Gateway ==='
kubectl get deployment,pod,svc -n envoy-gateway-system

echo
echo '=== Gateway API ==='
kubectl get gatewayclass
kubectl get gateway -A
kubectl get httproute -A

echo
echo '=== KServe ==='
kubectl get deployment,pod -n kserve
kubectl get crd | grep serving.kserve.io
kubectl api-resources --api-group=serving.kserve.io

echo
echo '=== KServe Ingress Gateway Conditions ==='
kubectl get gateway kserve-ingress-gateway \
  -n kserve \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status}{" ("}{.reason}{")\n"}{end}'

echo
echo '=== KServe Ingress Gateway Listener Conditions ==='
kubectl get gateway kserve-ingress-gateway \
  -n kserve \
  -o jsonpath='{range .status.listeners[*].conditions[*]}{.type}={.status}{" ("}{.reason}{")\n"}{end}'
