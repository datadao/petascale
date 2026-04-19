# petascale

Ambient observability for the creatures who live with you.

A Data Dao open source project.

> *An AI-native, edge-deployed, lakehouse-architected observability mesh for the modern multi-species household. We're operationalizing the household pet vertical at petabyte scale. (Currently: 340MB and two cats.)*

## Quick Start (Local Development)

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone and setup**:
   ```bash
   git clone <repo>
   cd petascale
   uv sync --extra dev
   ```

3. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your Home Assistant details
   ```

4. **Run tests**:
   ```bash
   PYTHONPATH=src uv run pytest tests/
   ```

5. **Run the daemon**:
   ```bash
   PYTHONPATH=src uv run python run_daemon.py
   ```

## Configuration

Create a `.env` file from `.env.example`:

```bash
# Required
HA_URL=http://your-ha-instance:8123
HA_TOKEN=your_long_lived_access_token  # Get from HA Settings > Security > Long-lived access tokens

# Optional (defaults provided)
MQTT_HOST=localhost
MQTT_PORT=1883
DB_PATH=petascale.db
MONITORED_SENSORS=sensor.litterbox_weight,sensor.food_bowl_weight
```

## Deployment to Proxmox

### Infrastructure Setup
1. Create Ubuntu LXC container (2GB RAM, 20GB SSD storage)
2. Mount local SSD storage at `/data` (NOT NFS!)
3. Install Python 3.12 and uv
4. Clone repository
5. Set up MQTT broker (Mosquitto) if not using HA's

### Secrets Management
**Option 1: Environment Variables in LXC Config**
```bash
# In Proxmox LXC configuration (/etc/pve/lxc/<id>.conf)
lxc.environment = HA_TOKEN=your_secret_token
lxc.environment = HA_URL=http://ha-ip:8123
lxc.environment = DB_PATH=/data/petascale.db
```

**Option 2: Secure .env file transfer**
```bash
# On your development machine
scp .env user@proxmox-lxc:/path/to/petascale/
```

**Option 3: Proxmox Secrets Management**
Use Proxmox's built-in secret storage or external secret managers.

### Deployment Steps
1. Set up LXC with local SSD storage
2. Install dependencies: `uv sync`
3. Configure environment variables
4. Create systemd service for the daemon
5. Enable monitoring and health checks

## Origin

Built because one of our cats got sick and started losing weight. Turned into a platform because that's what happens when data people try to help their pets.

## Taglines in the wild

Pick your chaos level:

- *Kubernetes for cats. Prometheus for dogs. SQLite for reality.*
- *I built this because my cat got sick and started losing weight, and I wanted to help him. Then I added a time-series database, a lakehouse, a streaming pipeline, and a DuckDB-WASM dashboard. He has gained 400 grams. I have gained a homelab.*
- *The pet that can be weighed is not the true pet. The weight that can be measured is 4.2 kilograms. Ambient telemetry for creatures who did not consent to becoming a time series, and never will.*
- *Enterprise-grade, zero-trust, event-driven pet observability with continuous WAL replication to object storage and sub-second hot-tier query visibility. Because your cat deserves SLA parity with your Kubernetes cluster. Scales from 1 cat to petabytes. We have tested the former.*

## License

TBD.
