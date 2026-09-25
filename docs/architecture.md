# Architecture

Cluster reference. Repo layout and quick stack summary: [README.md](../README.md).

**Crash-plan backup (node → NAS):** From your **workstation**, Ansible role `k3s_dr_backup` installs `k3s-dr-backup.timer` **on the k3s node**; that timer **rsyncs** `/var/lib/rancher/k3s/server/db/` (full cluster state), `/var/lib/rancher/k3s/storage/` (**local-path** PVC data: app DBs/configs), and `/etc/rancher/k3s/` into **`$nfs_mount_point/homelab-k3s-dr-backup/`** on the NAS. **Restore:** [disaster-recovery.md](disaster-recovery.md).

**Cluster backup (supplemental):** `infrastructure/cluster-backup/` — CronJob exports read-only YAML (Argo CD apps, cert-manager, SealedSecrets CRs, ingress, selected namespaces except `media`) to an **`nfs-client` PVC**. No raw `Secret` objects; **not** a substitute for the node DR mirror above.

**Velero (optional):** Helm chart sources live under `infrastructure/velero/` but there is no Argo CD Application for it; enable when you have object storage credentials and an operational backup plan.

---

## Network Topology

The cluster runs as a single-node k3s deployment on a local network (example: `192.168.1.0/24`). All services are accessed via `*.home` DNS entries pointing to the node IP.

### Host Network Services

These workloads use `hostNetwork: true`:

| Service | Reason |
|---------|--------|
| **nginx-ingress** | Binds directly to host ports 80 and 443 so that external HTTP/HTTPS traffic reaches the ingress controller without a cloud load balancer or extra port-forwarding. Uses `dnsPolicy: ClusterFirstWithHostNet` to retain access to cluster DNS. |
| **Plex** | Requires host networking for GDM (G'Day Mate discovery protocol) on UDP ports 32410-32414 and DLNA on port 1900. Without host networking, LAN clients cannot discover the Plex server. |
| **Home Assistant** | Uses host networking to communicate with IoT devices on the local network via mDNS, SSDP, and other discovery protocols that require direct L2 access. |

All other services use standard ClusterIP services and are accessed through nginx-ingress.

### Port Map

| Port | Protocol | Service | Notes |
|------|----------|---------|-------|
| 22 | TCP | SSH | Key-only, hardened via Ansible |
| 53 | TCP/UDP | Pi-hole DNS | LoadBalancer service type |
| 80 | TCP | nginx-ingress | Host port (HTTP) |
| 443 | TCP | nginx-ingress | Host port (HTTPS) |
| 6443 | TCP | k8s API server | k3s control plane |
| 6881 | TCP/UDP | qBittorrent | NodePort for BitTorrent traffic |
| 8472 | UDP | Flannel VXLAN | k3s CNI (internal) |
| 10250 | TCP | Kubelet API | k3s node communication |
| 32400 | TCP | Plex | Direct via hostNetwork |
| 51820 | UDP | Flannel WireGuard | k3s CNI (optional backend) |

---

## Namespace Layout

Each namespace represents a functional domain. This provides isolation boundaries for RBAC, network policy, and resource quotas.

| Namespace | Purpose | Contents |
|-----------|---------|----------|
| `argocd` | GitOps controller | Argo CD server, repo-server, application-controller, redis, **Argo CD Image Updater** |
| `cert-manager` | TLS automation | cert-manager controllers and CRDs |
| `infrastructure` | Cluster-wide services | Sealed Secrets controller, nginx-ingress controller, NFS provisioner |
| `monitoring` | Observability stack | Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics, **Loki**, **Promtail** |
| `dns` | DNS services | Pi-hole, AdGuard Home |
| `media` | Media management | Plex, Sonarr, Radarr, Prowlarr, qBittorrent, Overseerr, Autobrr, FlareSolverr |
| `dashboard` | Landing / portal | Homepage |
| `home-automation` | Smart home | Home Assistant |
| `security` | Security services | Vaultwarden |
| `kube-system` | k3s system components | CoreDNS, metrics-server, local-path-provisioner (disabled traefik/servicelb) |

---

## Storage Architecture

Storage is provided by a NAS appliance over NFS. There are two distinct access patterns:

### 1. Dynamic Provisioning (Application Configs)

The **NFS Subdir External Provisioner** creates PersistentVolumes on demand from a single NFS export.

```
NAS (nas.home)
  └── /volume1/k8s-data/                    # NFS export
        ├── infrastructure-grafana-pvc-.../   # Auto-created by provisioner
        ├── dns-pihole-config-pvc-.../
        ├── media-sonarr-config-pvc-.../
        └── ...
```

- **StorageClass**: `nfs-client` (set as default)
- **Reclaim Policy**: `Retain` -- PV data is kept after PVC deletion
- **Archive on Delete**: Enabled -- data is moved to `archived-*` prefix instead of deleted
- **Access Mode**: `ReadWriteOnce` for all application configs

Every application declares a PVC in its Helm chart. The provisioner automatically creates a subdirectory on the NAS for each PVC.

### 2. Direct NFS Mounts (Media Libraries)

Media-heavy services mount NFS volumes directly in their pod specs for access to large, shared media libraries. These are not managed by the provisioner.

```
NAS (nas.home)
  └── /volume1/data/
        ├── Movies/        # Mounted by Plex (read-only), Radarr (read-write)
        ├── Tv/            # Mounted by Plex (read-only), Sonarr (read-write)
        └── Downloads/     # Mounted by qBittorrent, Sonarr, Radarr
```

| Volume | Mounted By | Access |
|--------|-----------|--------|
| `/volume1/data/Movies` | Plex, Radarr | Plex: read-only; Radarr: read-write |
| `/volume1/data/Tv` | Plex, Sonarr | Plex: read-only; Sonarr: read-write |
| `/volume1/data/Downloads` | qBittorrent, Sonarr, Radarr | read-write |

Plex also uses an `emptyDir` volume (10 Gi limit) for transcoding scratch space, which is ephemeral and does not hit the NAS.

---

## Ingress Routing

nginx-ingress runs with `hostNetwork: true` on ports 80 and 443. All `*.home` hostnames resolve to the node IP via local DNS (Pi-hole or `/etc/hosts`).

| Hostname | Service | Namespace | Backend Port | Notes |
|----------|---------|-----------|-------------|-------|
| `argocd.home` | argocd-server | `argocd` | 443 (HTTPS) | SSL passthrough |
| `grafana.home` | grafana | `monitoring` | 80 | Datasources: Prometheus, Alertmanager, Loki — see `infrastructure/monitoring/values.yaml` and `infrastructure/monitoring/config/grafana-datasources.yaml` (reference) |
| `homepage.home` | homepage | `dashboard` | 3000 | Optional cluster ingress discovery (RBAC on pod) |
| `pihole.home` | pihole-web | `dns` | 80 | App root redirect to `/admin` |
| `adguard.home` | adguardhome web | `dns` | 80 (→ 3000) | Long-lived connections possible (ingress timeout annotation) |
| `sonarr.home` | sonarr | `media` | 8989 | |
| `radarr.home` | radarr | `media` | 7878 | |
| `prowlarr.home` | prowlarr | `media` | 9696 | |
| `qbit.home` | qbittorrent-web | `media` | 8080 | |
| `overseerr.home` | overseerr | `media` | 5055 | |
| `autobrr.home` | autobrr | `media` | 7474 | |
| `flaresolverr.home` | flaresolverr | `media` | 8191 | |
| `ha.home` | homeassistant | `home-automation` | 8123 | |
| `vaultwarden.home` | vaultwarden | `security` | 8070 | Security headers via annotation |

Plex is accessed directly at `http://<node-ip>:32400/web` (e.g. `http://192.168.1.10:32400/web` if the node is `192.168.1.10`) because it runs on hostNetwork and does not use ingress.

### Ingress Security Headers

The nginx-ingress controller is configured globally with:

- `ssl-redirect: true` -- Force HTTPS
- `hsts: true` with `max-age=31536000` and `includeSubDomains`
- `hide-headers: X-Powered-By, Server` -- Strip identifying headers
- `use-forwarded-headers: true` -- Respect upstream proxy headers

Vaultwarden adds per-ingress annotations for additional headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`).

---

## ArgoCD Sync Waves

The app-of-apps pattern uses sync-wave annotations to control deployment order. Lower (more negative) numbers deploy first.

```
Wave -4: Sealed Secrets         (must exist before any sealed secret can be decrypted)
Wave -3: cert-manager, nginx-ingress  (TLS CRDs/controller; ingress before Ingress resources)
Wave -2: NFS Provisioner        (must exist before many PVCs can be bound)
Wave -1: Monitoring (kube-prometheus-stack), Loki (logs; same namespace as Grafana)
Wave  0: Applications           (dns, media, dashboard, home-automation, security, …)
```

Argo CD Image Updater has no sync-wave annotation; it runs in `argocd` and can sync with other `argocd` workloads.

### Sync Policies

All Application CRs share these sync settings:

```yaml
syncPolicy:
  automated:
    prune: true       # Delete resources removed from git
    selfHeal: true    # Revert manual changes made outside git
  syncOptions:
    - CreateNamespace=true
```

The monitoring Application additionally uses `ServerSideApply=true` due to the large CRD manifests in kube-prometheus-stack that exceed the client-side apply annotation size limit.

### Sync Flow

```
1. Push commit to main branch
2. ArgoCD detects change (polling or webhook)
3. ArgoCD compares desired state (git) with live state (cluster)
4. Out-of-sync resources are applied in sync-wave order
5. Health checks verify each wave before proceeding
6. Pruning removes resources no longer in git
```

---

## Security Model

### Sealed Secrets Flow

```
Developer workstation                      k3s Cluster
┌─────────────────────┐                   ┌─────────────────────┐
│                     │                   │                     │
│  kubectl create     │                   │  Sealed Secrets     │
│  secret --dry-run   │                   │  Controller         │
│        │            │                   │        │            │
│        v            │                   │        v            │
│  kubeseal           │───── git ────────>│  SealedSecret CR    │
│  (encrypt with      │                   │  (decrypt with      │
│   cluster pub key)  │                   │   cluster priv key) │
│        │            │                   │        │            │
│        v            │                   │        v            │
│  .sealed.yaml       │                   │  Kubernetes Secret  │
│  (safe to commit)   │                   │  (used by pods)     │
└─────────────────────┘                   └─────────────────────┘
```

The `seal-secret.sh` helper script wraps this workflow into a single command:

```bash
./scripts/seal-secret.sh <secret-name> <namespace> <key> <value>
```

The sealed secret is scoped to its namespace -- it can only be decrypted by the controller and only applied in the namespace it was sealed for.

### SSH Hardening (Ansible security role)

| Setting | Value |
|---------|-------|
| `PermitRootLogin` | no |
| `PasswordAuthentication` | no |
| `MaxAuthTries` | 3 |
| `AllowUsers` | configured primary user only |
| SSH key directory permissions | 0700 |
| sshd_config file permissions | 0600 |

### Firewall Rules (UFW)

Default policy: **deny incoming, allow outgoing**.

Explicitly allowed:

| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH |
| 80 | TCP | HTTP |
| 443 | TCP | HTTPS |
| 6443 | TCP | k8s API |
| 53 | TCP/UDP | DNS |
| 32400 | TCP | Plex |
| 10250 | TCP | Kubelet |
| 8472 | UDP | Flannel VXLAN |
| 51820 | UDP | Flannel WireGuard |

Additionally, the k3s internal CIDRs are whitelisted:
- `10.42.0.0/16` -- pod CIDR
- `10.43.0.0/16` -- service CIDR

### fail2ban

Installed and configured via Ansible with a custom jail template. Protects SSH against brute-force attacks by banning source IPs after repeated failures.

### Container Security

- Non-root execution: workloads run as UID/GID 1000 where possible (linuxserver.io *arr images use s6-overlay and start as root briefly for PUID/PGID; see those charts’ values)
- `readOnlyRootFilesystem: true` applied to Vaultwarden
- Explicit capabilities: only Pi-hole receives `NET_ADMIN` and `NET_BIND_SERVICE`
- No `privileged: true` on any container
- All images pinned to specific version tags
- Resource requests and limits set on every container
