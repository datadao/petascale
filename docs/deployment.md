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

After creation, enable Docker support before first boot:

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

## 3. Bootstrap the LXC

```bash
pct start 100
pct enter 100

# Inside the LXC
apt-get update && apt-get install -y curl git ca-certificates

# Install Docker (official script)
curl -fsSL https://get.docker.com | sh
```

## 4. Deploy petascale

```bash
# Inside the LXC
git clone https://github.com/YOUR_ORG/petascale.git /opt/petascale
cd /opt/petascale

# Copy your .env (scp from dev machine or create manually)
cp .env.example .env
# Edit .env: set MQTT_HOST, HA_URL, HA_TOKEN

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

## 5. Verify

```bash
# Check daemon is running
docker compose ps

# Check data is flowing
sqlite3 /data/petascale/petascale.db \
  "SELECT count(*), datetime(max(timestamp)/1000,'unixepoch','localtime') FROM raw_measurements;"
```

## 6. Updates

```bash
cd /opt/petascale
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

## Useful commands

```bash
# Restart daemon
docker compose -f docker/docker-compose.yml restart

# Stop cleanly
docker compose -f docker/docker-compose.yml down

# Shell into container
docker exec -it petascale-daemon bash

# Run dashboard (from dev machine, not container)
uv run python scripts/dashboard.py --db /mnt/data/petascale/petascale.db
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
