#!/bin/bash
# blueprint-mount.sh — apply blueprint via ConfigMap mount on the live deployment
# (no repo PR needed): creates cm authentik-blueprints from the local file and
# patches the deployment to mount it at /blueprints/custom.
HOST=${HOST:-tbaltzakis@100.74.191.58}; PASS=${OMV_SUDO_PASS:?set OMV_SUDO_PASS}
BP=${1:-/home/tbaltzakis/omv-infrastructure/omv/authentik/blueprint.yaml}
scp -q "$BP" $HOST:/tmp/cloudless-platform.yaml
ssh -o BatchMode=yes $HOST "echo $PASS | sudo -S sh -c '
  k3s kubectl -n authentik create configmap authentik-blueprints --from-file=/tmp/cloudless-platform.yaml --dry-run=client -o yaml | k3s kubectl apply -f - &&
  k3s kubectl -n authentik patch deploy authentik-server --type=json -p \"[{op:add,path:/spec/template/spec/volumes/-,value:{name:bp,configMap:{name:authentik-blueprints}}},{op:add,path:/spec/template/spec/containers/0/volumeMounts/-,value:{name:bp,mountPath:/blueprints/custom}}]\" &&
  k3s kubectl -n authentik rollout status deploy/authentik-server --timeout=120s'"
