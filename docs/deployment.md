# Deployment — Proxmox homelab

Target: single Debian 12 LXC on Proxmox 8.x with Docker.
The MQTT broker runs in a separate LXC. HA runs as a QEMU VM.

## 1. Create the LXC in Proxmox

In the Proxmox web UI (or via CLI):

- **Template:** Debian 12 (bookworm)
- **CPU:** 1 core (2 if you want headroom)
- **RAM:** 512 MB
- **Storage:** 4 GB for the root disk (OS + Docker images)
- **Network:** bridge on your LAN, static IP recommended

After creation, before starting:

- **Firewall tab:** ensure firewall is **disabled** for this LXC
- **Network tab:** set IPv4 to **DHCP**

Then enable Docker support:

```bash
# On the Proxmox host — replace 100 with your LXC ID
pct set 100 --features keyctl=1,nesting=1
```

> If using an unprivileged container (default), `nesting=1` is required for Docker.
> If you run into cgroup issues, set `--unprivileged 0` to make it privileged.

## 2. Mount local SSD for data

Proxmox local SSD is the right place for SQLite — never NFS.
Add a bind mount point inside the LXC:

```bash
# On the Proxmox host
mkdir -p /mnt/data/petascale          # path on host SSD
pct set 100 --mp0 /mnt/data/petascale,mp=/data/petascale
```

The daemon writes `petascale.db` to `/data/petascale/` inside the container.

## 3. Fix networking if eth0 is DOWN on first boot

If the container has no network after first boot, bring it up manually:

```bash
ip link set eth0 up
dhclient eth0
echo "nameserver 8.8.8.8" > /etc/resolv.conf
```

To make it persistent, edit `/etc/network/interfaces`:

```bash
cat >> /etc/network/interfaces << 'EOF'

auto eth0
iface eth0 inet dhcp
EOF
```

## 4. Bootstrap the LXC

```bash
pct start 104
pct enter 104

# Inside the LXC
apt-get update && apt-get install -y curl git ca-certificates avahi-daemon

# Enable mDNS so petascale-ingest.local resolves on the LAN
systemctl enable avahi-daemon
systemctl start avahi-daemon

# Install Docker (official script)
curl -fsSL https://get.docker.com | sh
```

## 5. Deploy petascale

```bash
# Inside the LXC
git clone https://github.com/YOUR_ORG/petascale.git /opt/petascale
cd /opt/petascale

# Copy your .env (scp from dev machine or create manually)
cp .env.example .env
# Edit .env: set MQTT_HOST, HA_URL, HA_TOKEN

# Cat avatars (optional — fallback is an initial-letter circle)
# Copy locally-stored avatars (gitignored under .private/) onto the host,
# then they're picked up by the dashboard container automatically:
#   from dev:  scp .private/avatars/cat-*.png petascale-ingest.local:/tmp/
#   on LXC:    mkdir -p /mnt/data/petascale/avatars && \
#              mv /tmp/cat-*.png /mnt/data/petascale/avatars/
# The committed config references .private/avatars/<file> for local preview;
# the dashboard falls back to /data/avatars/<basename> in the container.

# Build and start
cd docker
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f
# or via journald:
journalctl -t petascale -f
```

## 6. Verify

```bash
# Install sqlite3 if not present
apt-get install -y sqlite3

# Check daemon is running
docker compose ps

# Check total row count and latest timestamp
sqlite3 /data/petascale/petascale.db \
  "SELECT count(*), datetime(max(timestamp)/1000,'unixepoch','localtime') FROM raw_measurements;"

# Check rows per day
sqlite3 /data/petascale/petascale.db \
  "SELECT date(timestamp/1000,'unixepoch','localtime') as day, count(*) FROM raw_measurements GROUP BY day ORDER BY day;"
```

## 7. TrueNAS SMB backup

### On TrueNAS

1. Create a dataset: `tank/petascale` (or whatever pool you use)
2. Create an SMB share pointing at that dataset, share name: `petascale`
3. Create a local TrueNAS user `petascale-backup` with a strong password
4. Grant that user read/write access to the dataset

### On the LXC

Install cifs-utils:

```bash
apt-get install -y cifs-utils rsync
```

Create the credentials file (never commit this):

```bash
cat > /etc/petascale-smb-credentials << 'EOF'
username=petascale-backup
password=REPLACE_ME
EOF
chmod 600 /etc/petascale-smb-credentials
```

Install and enable the systemd units:

```bash
cp /opt/petascale/infra/systemd/petascale-backup.service /etc/systemd/system/
cp /opt/petascale/infra/systemd/petascale-backup.timer   /etc/systemd/system/
chmod +x /opt/petascale/infra/systemd/petascale-backup.sh

systemctl daemon-reload
systemctl enable --now petascale-backup.timer
```

Run a manual backup to verify:

```bash
systemctl start petascale-backup.service
journalctl -u petascale-backup -n 50
```

The timer runs nightly at 03:00 (+up to 10 min random delay). It syncs
`/data/petascale/` → `//truenas.local/petascale/` excluding `dashboard.html`.

---

## 8. Updates

```bash
cd /opt/petascale
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

## Useful commands

```bash
# Restart all services
docker compose -f docker/docker-compose.yml restart

# Stop cleanly
docker compose -f docker/docker-compose.yml down

# Shell into daemon container
docker exec -it petascale-daemon bash

# View dashboard logs (regenerates every 5 min)
docker compose -f docker/docker-compose.yml logs -f analytics

# Dashboard is served at http://petascale-ingest.local:8080

# Run dashboard manually from dev machine
uv run --extra analytics python scripts/dashboard.py \
    --db /mnt/data/petascale/petascale.db \
    --archive /mnt/data/petascale/archive
```

## Network topology

```
ESP32 sensors
    │ MQTT publish
    ▼
[LXC: Mosquitto]  ←──────────────────────────────┐
    │ sensors/#                                    │
    ▼                                              │
[LXC: petascale-daemon]                           │
    │ writes                     petascale/events/#│
    ▼                                              │
/data/petascale/petascale.db   ────────────────────┘
    │
    ▼
[QEMU: Home Assistant]  (backfill source + event consumer)
```
