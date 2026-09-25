# Disaster recovery (crash plan)

Goal: replace the **k3s node** (the homelab machine) and bring the cluster back so **workloads, databases, and configs** match the last good backup — **without** re-seeding every app by hand.

## Where each piece runs

| Piece | Runs on | Role |
|--------|---------|------|
| **Ansible** (`ansible-playbook …`) | Your **laptop / workstation** (control node) | SSH into the homelab host and install or update files, systemd units, packages |
| **`k3s-dr-backup.timer`** + script | The **k3s server** (homelab node) | Fires on a schedule **on that machine** and rsyncs k3s data to the NAS mount (`nfs_mount_point` on the node) |

There is no mismatch: you **push** the timer definition from the laptop once (or whenever you change the role); after that, backups run **autonomously on the node** without your laptop being on.

This stack uses **two** backup layers:

| Layer | What it is | What it gives you |
|--------|------------|-------------------|
| **Node DR backup** (Ansible deploys; systemd runs on node) | `k3s-dr-backup.timer` on the **homelab host** copies k3s state to **NAS** | **Full API state** (all Secrets, PVC bindings) + **local-path PVC files** (Sonarr/Radarr DBs, Pi-hole, Vaultwarden, Home Assistant, Loki, etc.) + k3s config |
| **cluster-backup** (CronJob in cluster) | YAML dumps to an **nfs-client PVC** | Human-readable exports of many CRs; **no** raw `Secret` objects; **no** replacement for node DR |

**Media libraries** on the NAS are already on the NAS; you are not duplicating them in these jobs.

---

## Where the DR files land

On the **NAS**, under your existing NFS export (same tree as `nfs_mount_point` on the node, e.g. `/mnt/nfs`):

```text
homelab-k3s-dr-backup/
  current/                    ← latest mirror (use this for restore)
    server-db/                ← etcd/SQLite + snapshots (cluster state)
    storage/                  ← local-path provisioner data (app DBs & configs)
    etc-rancher-k3s/          ← k3s config
  history/<UTC-timestamp>/    ← rolling copies of server/db only (default 7 days)
```

Install path on the node: `/usr/local/sbin/k3s-dr-backup.sh`  
Timer: `k3s-dr-backup.timer` (default **02:15** daily; change via Ansible `k3s_dr_backup_on_calendar`).

Manual run **on the homelab node** (SSH in, or a one-off Ansible ad-hoc):

```bash
sudo /usr/local/sbin/k3s-dr-backup.sh
```

---

## Restore on a new machine (same NAS, same export path)

1. **Install the OS** and run Ansible so **NFS is mounted** and **k3s is installed** (e.g. `playbooks/site.yml`), **or** install k3s manually to match your version.
2. **Stop k3s** before overwriting its data:
   ```bash
   sudo systemctl stop k3s
   ```
3. **Restore** from `current/` (adjust if your mount is not `/mnt/nfs`):
   ```bash
   sudo mkdir -p /var/lib/rancher/k3s/server/db /var/lib/rancher/k3s/storage /etc/rancher/k3s
   sudo rsync -a /mnt/nfs/homelab-k3s-dr-backup/current/server-db/ /var/lib/rancher/k3s/server/db/
   sudo rsync -a /mnt/nfs/homelab-k3s-dr-backup/current/storage/   /var/lib/rancher/k3s/storage/
   sudo rsync -a /mnt/nfs/homelab-k3s-dr-backup/current/etc-rancher-k3s/ /etc/rancher/k3s/
   ```
4. Fix ownership if needed:
   ```bash
   sudo chown -R root:root /var/lib/rancher/k3s /etc/rancher/k3s
   ```
5. **Start k3s**:
   ```bash
   sudo systemctl start k3s
   ```
6. **Verify**:
   ```bash
   sudo k3s kubectl get nodes
   sudo k3s kubectl get pods -A
   ```

If the API server fails or reports datastore errors, use the official **k3s backup/restore** flow with a snapshot under `server-db/snapshots/`:

- [K3s backup and restore](https://docs.k3s.io/datastore/backup-restore)

Typical pattern: stop k3s, run `k3s server --cluster-reset --cluster-reset-restore-path=...` with the path to a `.db` snapshot from your backup, wait until it exits, then `systemctl start k3s` again.

---

## If you have **no** node DR backup

- **Git + `bootstrap.sh` + Argo** can recreate **deployments** from the repo.
- **nfs-client PVC data** for apps (Grafana, qBittorrent config, etc.) still lives on the NAS under the provisioner paths; new PVCs may create **new** subdirectories — you may need to **repoint or copy** data for those apps.
- Anything that used **`local-path-homelab`** (Sonarr, Pi-hole, Vaultwarden, Home Assistant, Loki, …) **loses its disk state** unless you restore `current/storage/` from a backup.

---

## Sealed Secrets

- Restoring **`server-db`** restores the cluster’s **Secrets**, including the Sealed Secrets controller key — existing **SealedSecret** resources keep working.
- If you rebuild an **empty** cluster from Git only, use the **offline** controller key backup described in [setup.md](setup.md) (Sealed Secrets section).

---

## Ansible (on your laptop)

From your **workstation** (repo clone + inventory pointing at the homelab host):

```bash
cd homelab/ansible && ansible-playbook playbooks/k3s-dr-backup.yml
```

Or full **`site.yml`** (after `k3s`) to install the timer the first time. That only **configures** the node; it does not need to run daily.

Variables: `roles/k3s_dr_backup/defaults/main.yml` (`k3s_dr_backup_dest_dir`, schedule, retention).

---

## What still depends on you

- **Stable NAS** and **correct NFS export** path in inventory (`nfs_base_export`, `nfs_mount_point`).
- **Git remote** URLs in `argocd-apps/` if the repo moves.
- **DNS / TLS**: same `*.home` and node IP (or update ingress/DNS after restore).

This gets you as close as reasonable to “new laptop, same cluster” **as long as the NAS backup folder is current** and you restore **before** relying on a fresh empty k3s data directory.
