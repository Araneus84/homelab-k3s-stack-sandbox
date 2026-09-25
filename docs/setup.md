# Setup Guide

Step-by-step provisioning from scratch. **Example IPs** in this doc use `192.168.1.0/24` (e.g. node `192.168.1.10`, NAS `192.168.1.11`); substitute your LAN.

See [README.md](../README.md) for repo layout and [architecture.md](architecture.md) for cluster detail.

---

## Prerequisites

### Hardware / Infrastructure

- **Server**: Ubuntu 22.04+ (physical machine or VM). Minimum 4 CPU cores, 8 GB RAM, 50 GB disk. The server should have a static IP on your local network (e.g., 192.168.1.10).
- **NAS**: Any NAS appliance (Synology, TrueNAS, QNAP, etc.) with NFS exports enabled. Required exports:
  - A general-purpose export for Kubernetes PVCs (e.g., `/volume1/k8s-data`)
  - Media exports for Plex/Sonarr/Radarr (e.g., `/volume1/data/Movies`, `/volume1/data/Tv`, `/volume1/data/Downloads`)
- **Control machine**: Your laptop or desktop. macOS, Linux, or WSL2 on Windows all work.

### Software on the Control Machine

| Tool | Purpose | Install |
|------|---------|---------|
| Ansible | Server provisioning | `pip install ansible` |
| kubectl | Kubernetes CLI | [Install guide](https://kubernetes.io/docs/tasks/tools/) |
| helm | Helm CLI | `curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \| bash` |
| kubeseal | Sealed Secrets CLI | See [kubeseal installation](#install-kubeseal) below |

---

## 1. SSH Key Setup

If you do not already have an SSH key pair for the homelab server, generate one:

```bash
ssh-keygen -t ed25519 -C "homelab" -f ~/.ssh/homelab_key
```

Copy the public key to the server:

```bash
ssh-copy-id -i ~/.ssh/homelab_key.pub your-user@192.168.1.10
```

Verify passwordless login works:

```bash
ssh -i ~/.ssh/homelab_key your-user@192.168.1.10
```

---

## 2. Ansible Installation

Install Ansible on your control machine:

```bash
# Using pip (recommended)
pip install ansible

# Verify
ansible --version
```

Install required Ansible collections:

```bash
ansible-galaxy collection install ansible.posix community.general
```

---

## 3. Configure the Inventory

```bash
cd ansible
cp inventory/hosts.yml.example inventory/hosts.yml
```

Edit `inventory/hosts.yml`:

```yaml
all:
  hosts:
    homelab:
      ansible_host: "192.168.1.10"              # Your server IP
      ansible_user: "user"                        # Your SSH username
      ansible_ssh_private_key_file: "~/.ssh/homelab_key"
      ansible_python_interpreter: /usr/bin/python3
  children:
    k3s_servers:
      hosts:
        homelab:
```

Edit `inventory/group_vars/all.yml` and update:

```yaml
# Your NAS IP and export paths
nfs_server: "nas.home"
nfs_base_export: /volume1/data

# Your SSH user
primary_user: user

# Your timezone
timezone: America/New_York
```

Test connectivity:

```bash
ansible all -m ping
```

Expected output:

```
homelab | SUCCESS => {
    "ping": "pong"
}
```

---

## 4. Run the Ansible Playbooks

### Full provisioning (recommended for first run)

```bash
ansible-playbook playbooks/site.yml
```

This runs all roles in order:

1. **common** -- Sets hostname, timezone, installs packages, disables swap, configures sysctl for Kubernetes, loads kernel modules
2. **security** -- Deploys hardened sshd_config, configures UFW firewall with deny-by-default policy, installs and configures fail2ban
3. **nfs** -- Installs NFS client packages, creates mount points, adds fstab entries, mounts and verifies NFS shares
4. **k3s** -- Downloads and installs k3s (with traefik and servicelb disabled), installs Helm, creates all application namespaces

### Individual playbooks

If you need to run specific stages:

```bash
# OS setup only (no k3s)
ansible-playbook playbooks/setup.yml

# k3s only (assumes OS is configured)
ansible-playbook playbooks/k3s.yml

# Using tags to run specific roles
ansible-playbook playbooks/site.yml --tags security
ansible-playbook playbooks/site.yml --tags k3s
```

---

## 5. Copy the Kubeconfig

After Ansible completes, copy the kubeconfig to your control machine:

```bash
scp your-user@192.168.1.10:/etc/rancher/k3s/k3s.yaml ~/.kube/config
```

Update the server address from `127.0.0.1` to your actual server IP:

```bash
sed -i 's/127.0.0.1/192.168.1.10/g' ~/.kube/config
```

On macOS, use `sed -i ''` instead of `sed -i`.

Verify:

```bash
kubectl get nodes
```

Expected output:

```
NAME      STATUS   ROLES                  AGE   VERSION
homelab   Ready    control-plane,master   1m    v1.31.4+k3s1
```

---

## 6. Bootstrap Script

The bootstrap script installs the two components that must exist before ArgoCD can manage itself: Sealed Secrets and ArgoCD.

**Before running**, update the git repository URL in all Application CR files:

- `argocd-apps/app-of-apps.yaml`
- All files in `argocd-apps/infrastructure/`
- All files in `argocd-apps/apps/`

Replace `https://github.com/YOUR_USERNAME/homelab.git` with your actual repository URL.

Then run:

```bash
./scripts/bootstrap.sh
```

The script performs these steps:

| Step | Action |
|------|--------|
| 1/6 | Verifies `kubectl` can reach the cluster |
| 2/6 | Creates all namespaces (infrastructure, media, dns, home-automation, security, monitoring, dashboard, argocd; `cert-manager` is created by Argo CD when that Application syncs) |
| 3/6 | Installs Sealed Secrets controller via Helm into the `infrastructure` namespace |
| 4/6 | Installs ArgoCD via Helm into the `argocd` namespace using values from `infrastructure/argocd/values.yaml` |
| 5/6 | Retrieves and displays the ArgoCD initial admin password |
| 6/6 | Prompts to apply the app-of-apps root Application |

Save the ArgoCD admin password displayed in step 5. Change it after first login:

```bash
argocd login argocd.home
argocd account update-password
```

---

## 7. Install kubeseal

kubeseal is the CLI tool for encrypting secrets with the Sealed Secrets controller's public key.

```bash
# Linux amd64
KUBESEAL_VERSION=0.27.3
wget "https://github.com/bitnami-labs/sealed-secrets/releases/download/v${KUBESEAL_VERSION}/kubeseal-${KUBESEAL_VERSION}-linux-amd64.tar.gz"
tar -xvzf "kubeseal-${KUBESEAL_VERSION}-linux-amd64.tar.gz"
sudo install -m 755 kubeseal /usr/local/bin/kubeseal

# macOS (via Homebrew)
brew install kubeseal

# Verify
kubeseal --version
```

---

## 8. Create Sealed Secrets

Each service that requires credentials needs a sealed secret created and applied. Use the helper script:

### Pi-hole admin password

```bash
./scripts/seal-secret.sh pihole-admin dns password 'your-pihole-admin-password'
kubectl apply -f pihole-admin.sealed.yaml
```

### Grafana admin credentials

```bash
./scripts/seal-secret.sh grafana-admin-credentials monitoring admin-password 'your-grafana-password'
kubectl apply -f grafana-admin-credentials.sealed.yaml
```

Note: The Grafana admin username is set to `admin` in the Helm values. Only the password needs a sealed secret.

### Vaultwarden admin token

```bash
./scripts/seal-secret.sh vaultwarden-admin security admin-token 'your-vaultwarden-admin-token'
kubectl apply -f vaultwarden-admin.sealed.yaml
```

### Committing sealed secrets

The `.sealed.yaml` files are safe to commit to git -- they are encrypted with the cluster's public key and can only be decrypted by the Sealed Secrets controller running inside the cluster. You can optionally move them into the appropriate app's `templates/` directory for ArgoCD to manage.

---

## 9. Verify Deployments

### Check ArgoCD

Open `https://argocd.home` in your browser (or use the node IP if DNS is not yet configured). Log in with `admin` and the password from the bootstrap step.

All applications should appear and eventually show as "Synced" and "Healthy". Infrastructure apps sync first due to sync waves.

### Check via kubectl

```bash
# All pods across all namespaces
kubectl get pods -A

# Infrastructure
kubectl get pods -n infrastructure
kubectl get pods -n monitoring
kubectl get pods -n argocd

# Applications
kubectl get pods -n dns
kubectl get pods -n media
kubectl get pods -n dashboard
kubectl get pods -n home-automation
kubectl get pods -n security

# Check ingresses
kubectl get ingress -A

# Check PVCs
kubectl get pvc -A

# Check that the NFS storage class exists and is default
kubectl get storageclass
```

---

## 10. DNS Configuration

For `*.home` hostnames to resolve to your cluster, you need local DNS configuration.

### Option A: Pi-hole as network DNS (recommended)

Once Pi-hole is running, add local DNS records in the Pi-hole admin panel at `http://pihole.home/admin`:

1. Go to **Local DNS** > **DNS Records**
2. Add entries for each service:

   | Domain | IP |
   |--------|----|
   | `pihole.home` | 192.168.1.10 |
   | `adguard.home` | 192.168.1.10 |
   | `homepage.home` | 192.168.1.10 |
   | `argocd.home` | 192.168.1.10 |
   | `grafana.home` | 192.168.1.10 |
   | `sonarr.home` | 192.168.1.10 |
   | `radarr.home` | 192.168.1.10 |
   | `prowlarr.home` | 192.168.1.10 |
   | `qbit.home` | 192.168.1.10 |
   | `overseerr.home` | 192.168.1.10 |
   | `autobrr.home` | 192.168.1.10 |
   | `flaresolverr.home` | 192.168.1.10 |
   | `ha.home` | 192.168.1.10 |
   | `vaultwarden.home` | 192.168.1.10 |

3. Set your router's DHCP DNS server to the Pi-hole IP (192.168.1.10) so all devices on the network use it.

### Option B: /etc/hosts (per-machine)

If you do not want to use Pi-hole as your network DNS, add entries to `/etc/hosts` on each machine that needs access:

```
192.168.1.10  pihole.home adguard.home homepage.home argocd.home grafana.home sonarr.home radarr.home prowlarr.home qbit.home overseerr.home autobrr.home flaresolverr.home ha.home vaultwarden.home
```

### Plex

Plex does not use ingress -- it runs on hostNetwork and is accessed directly at:

```
http://192.168.1.10:32400/web
```

No DNS entry is needed for Plex.

---

## Troubleshooting

### Ansible cannot connect to the server

```
fatal: [homelab]: UNREACHABLE! => {"msg": "Failed to connect to the host via ssh"}
```

- Verify the IP in `hosts.yml` is correct
- Verify SSH key path: `ssh -i ~/.ssh/homelab_key your-user@192.168.1.10`
- Ensure the SSH key was copied: `ssh-copy-id -i ~/.ssh/homelab_key.pub your-user@192.168.1.10`
- Check that the server is reachable: `ping 192.168.1.10`

### k3s fails to install

```
TASK [k3s : Install k3s] fatal: ...
```

- Ensure swap is disabled: `swapon --show` should produce no output
- Check that required kernel modules are loaded: `lsmod | grep br_netfilter`
- Check system requirements: at least 4 CPU cores and 8 GB RAM
- Review k3s logs: `journalctl -u k3s -f`

### Pods stuck in Pending (PVC not binding)

```
NAME         STATUS    RESTARTS   AGE
my-pod       Pending   0          5m
```

- Check PVC status: `kubectl get pvc -n <namespace>`
- If PVC is in `Pending` state, verify the NFS provisioner is running: `kubectl get pods -n infrastructure`
- Verify NFS server is reachable from the node: `showmount -e nas.home`
- Check NFS provisioner logs: `kubectl logs -n infrastructure -l app=nfs-subdir-external-provisioner`
- Verify the NFS export path in `infrastructure/nfs-provisioner/values.yaml` is correct

### ArgoCD shows "Unknown" or "OutOfSync"

- Check ArgoCD application controller logs: `kubectl logs -n argocd -l app.kubernetes.io/component=application-controller`
- Verify the git repository URL is correct in all Application CRs
- Ensure the repo is public, or configure ArgoCD with repository credentials
- Try a manual sync from the ArgoCD UI to see detailed error messages

### Pi-hole DNS not responding

- Verify Pi-hole pod is running: `kubectl get pods -n dns`
- Check that the LoadBalancer service has an external IP: `kubectl get svc -n dns`
- On bare-metal k3s, Pi-hole's DNS service uses a LoadBalancer type. If k3s's built-in klipper-lb is disabled, you may need to set `loadBalancerIP` to your node IP in the Pi-hole values.
- Test DNS directly: `dig @192.168.1.10 google.com`

### nginx-ingress not reachable on ports 80/443

- Verify the ingress controller pod is running: `kubectl get pods -n infrastructure -l app.kubernetes.io/name=ingress-nginx`
- Check that `hostNetwork: true` is set in the nginx values
- Verify no other process is binding ports 80/443 on the host: `ss -tlnp | grep -E ':80|:443'`
- Check UFW allows ports 80 and 443: `sudo ufw status`

### Sealed secret not decrypting

- Verify the Sealed Secrets controller is running: `kubectl get pods -n infrastructure -l app.kubernetes.io/name=sealed-secrets`
- Ensure the sealed secret was created for the correct namespace (sealed secrets are namespace-scoped)
- Re-fetch the public key and re-seal if the controller was reinstalled: `kubeseal --fetch-cert --controller-namespace infrastructure --controller-name sealed-secrets`
- Check controller logs: `kubectl logs -n infrastructure -l app.kubernetes.io/name=sealed-secrets`

### Home Assistant not discovering devices

- Verify Home Assistant is running with `hostNetwork: true` in its values
- Check that `dnsPolicy: ClusterFirstWithHostNet` is set so HA can still resolve cluster services
- Ensure mDNS/SSDP traffic is not blocked by UFW. You may need to allow UDP port 5353 (mDNS) and 1900 (SSDP) if device discovery fails.

---

## Maintenance

### System updates

Run the maintenance playbook to update packages, rotate logs, and verify system health:

```bash
cd ansible
ansible-playbook playbooks/maintenance.yml
```

This can be scheduled via cron on the control machine:

```cron
0 3 * * 0 cd /path/to/homelab/ansible && ansible-playbook playbooks/maintenance.yml
```

The maintenance role also installs **`k3s-image-prune.timer`** on each k3s node: weekly `k3s crictl rmi --prune` removes **unused** images from containerd (safe for running pods). Override schedule with `maintenance_image_prune_on_calendar` or set `maintenance_image_prune_timer_enabled: false` in Ansible vars to turn it off. Check status with `systemctl list-timers k3s-image-prune.timer`.

### Disaster recovery (crash plan)

Replacing the node and restoring **cluster state, databases, and local-path configs** is documented in **[disaster-recovery.md](disaster-recovery.md)**. Run Ansible **from your laptop** (`playbooks/site.yml` or `k3s-dr-backup.yml`) to install **`k3s-dr-backup.timer`** on the **homelab host**; the timer then runs **on the server** and mirrors k3s data to **`$nfs_mount_point/homelab-k3s-dr-backup/`**. The in-cluster **cluster-backup** CronJob is only a supplemental YAML export.

### Updating application versions

1. Update the image tag in the app's `values.yaml` (or rely on **Argo CD Image Updater** where annotations are set on the Application, e.g. Homepage and FlareSolverr).
2. Commit and push to `main`
3. Argo CD detects the change and rolls out the new version

### Backing up sealed secrets keys

The Sealed Secrets controller stores its encryption key as a Kubernetes secret. If the controller is deleted and recreated, existing sealed secrets cannot be decrypted without this key.

Back up the master key:

```bash
kubectl get secret -n infrastructure -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > sealed-secrets-master-key.yaml
```

Store this file securely (password manager, encrypted USB, etc.). **Do not commit it to git.**

To restore after a cluster rebuild:

```bash
kubectl apply -f sealed-secrets-master-key.yaml
kubectl delete pod -n infrastructure -l app.kubernetes.io/name=sealed-secrets
```
