#!/usr/bin/env bash
set -euo pipefail
# seal-secret.sh <secret-name> <namespace> <key> <value>
# Needs: kubectl, kubeseal, Sealed Secrets controller in cluster (see docs/setup.md).
KUBESEAL_VERSION="${KUBESEAL_VERSION:-0.27.3}"

SECRET_NAME="${1:?Usage: seal-secret.sh <secret-name> <namespace> <key> <value>}"
NAMESPACE="${2:?Usage: seal-secret.sh <secret-name> <namespace> <key> <value>}"
KEY="${3:?Usage: seal-secret.sh <secret-name> <namespace> <key> <value>}"
VALUE="${4:?Usage: seal-secret.sh <secret-name> <namespace> <key> <value>}"

# Check for kubeseal
if ! command -v kubeseal &>/dev/null; then
    echo "ERROR: kubeseal is not installed. Install instructions: docs/setup.md (Install kubeseal)."
    echo "Example (Linux amd64, version ${KUBESEAL_VERSION}):"
    echo "  wget https://github.com/bitnami-labs/sealed-secrets/releases/download/v${KUBESEAL_VERSION}/kubeseal-${KUBESEAL_VERSION}-linux-amd64.tar.gz"
    echo "  tar -xvzf kubeseal-${KUBESEAL_VERSION}-linux-amd64.tar.gz && sudo install -m 755 kubeseal /usr/local/bin/kubeseal"
    exit 1
fi

echo "Creating sealed secret:"
echo "  Name:      $SECRET_NAME"
echo "  Namespace: $NAMESPACE"
echo "  Key:       $KEY"
echo "  Value:     ********"
echo ""

# Create the secret and seal it
kubectl create secret generic "$SECRET_NAME" \
    --namespace "$NAMESPACE" \
    --from-literal="$KEY=$VALUE" \
    --dry-run=client -o yaml | \
    kubeseal \
        --controller-namespace infrastructure \
        --controller-name sealed-secrets \
        --format yaml > "${SECRET_NAME}.sealed.yaml"

echo "Sealed secret written to: ${SECRET_NAME}.sealed.yaml"
echo ""
echo "Apply it with:"
echo "  kubectl apply -f ${SECRET_NAME}.sealed.yaml"
echo ""
echo "Or add the .sealed.yaml file to git when you commit (encrypted for this cluster only)."
