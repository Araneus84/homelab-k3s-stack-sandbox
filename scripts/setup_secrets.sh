#!/usr/bin/env bash
set -euo pipefail
# Interactive prompts → SealedSecrets via seal-secret.sh

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR=$(dirname "$0")
SEAL_HELPER="${SCRIPT_DIR}/seal-secret.sh"

echo -e "${BLUE}Homelab secrets → SealedSecret files${NC}"
echo "Enter values when prompted (empty = skip)."
echo ""

check_prereqs() {
    if ! command -v kubeseal &>/dev/null; then
        echo -e "${YELLOW}kubeseal not found.${NC} Install per docs/setup.md"
        exit 1
    fi
}

seal_it() {
    local secret_name=$1
    local namespace=$2
    local key=$3
    local prompt=$4

    echo ""
    echo -e "${YELLOW}${secret_name} (${namespace})${NC}"
    read -sp "$prompt: " secret_value
    echo ""

    if [[ -z "$secret_value" ]]; then
        echo "Skipping..."
        return
    fi

    "$SEAL_HELPER" "$secret_name" "$namespace" "$key" "$secret_value"
    echo -e "${GREEN}Wrote ${secret_name}.sealed.yaml${NC}"
}

check_prereqs

seal_it "pihole-admin" "dns" "password" "Pi-hole Web Admin Password"
seal_it "grafana-admin-credentials" "monitoring" "admin-password" "Grafana Admin Password"
seal_it "vaultwarden-admin" "security" "admin-token" "Vaultwarden Admin Token"

echo ""
echo -e "${BLUE}Optional${NC}"
echo "Plex claim: https://www.plex.tv/claim/ (short-lived token)"
seal_it "plex-claim" "media" "claimToken" "Plex Claim Token"

echo ""
echo -e "${GREEN}Done.${NC} Apply with: kubectl apply -f *.sealed.yaml"
ls -1 *.sealed.yaml 2>/dev/null || true
