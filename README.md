# Paddle Rack

Paddle Rack is a streamlined deployment and management system for running multiple Paddler instances, each paired with a llama.cpp model (e.g., xxxs, xxs, xs, s, m, l, xl, xxl, xxxl). Designed for both local test environments and distributed setups (like Raspberry Pi clusters), Paddle Rack automates the deployment of Paddler load balancers, agents, and llama.cpp instances using systemd template units and model-specific configurations stored in /etc/paddler/models. With a simple command-line interface (paddler-deploy), it orchestrates service lifecycle management, while a centralized monitoring system (paddler_manager) aggregates metrics and API data (/api/v1/agents) into a Grafana dashboard for a unified view of all instances. Paddle Rack combines the flexibility of systemd with intuitive configuration, making it easy to manage and monitor your AI model infrastructure.

### Overview

- **Objective**: Automate the deployment and management of 3–9 Paddler instances, each tied to a `llama.cpp` model (xxxs, xxs, xs, s, m, l, xl, xxl, xxxl), with centralized monitoring.
- **Configuration**: Store model-specific configs in `/etc/paddler/models` (e.g., `xxxs.yaml`), replacing the previous `/etc/paddler/config`.
- **Systemd Templates**: Use static `paddler@.service`, `paddler-agent@.service`, and `llama-cpp@.service` templates to manage services, with `%i` mapping to model names or model-agent pairs.
- **Paddler-Deploy CLI**: Validates configs, enables/disables systemd services, deploys remote agents (via SSH for distributed setups), and generates `inventory.yaml` for monitoring.
- **Paddler Manager**: Monitors all instances via `/api/v1/agents` and StatsD, exposing metrics to Prometheus and visualizing in Grafana.
- **Test vs. Production**: Supports local test setup (all on one machine) and scales to distributed setups with minimal changes.

### Implementation Details

#### 1. **Models Directory**

The configuration directory is `/etc/paddler/models`, with one YAML file per model.

**Directory Structure**:

```
/etc/paddler/models/
├── xxxs.yaml
├── xxs.yaml
├── xs.yaml
├── s.yaml
├── m.yaml
├── l.yaml
├── xl.yaml
├── xxl.yaml
├── xxxl.yaml
```

**Example Config: `/etc/paddler/models/xxxs.yaml`**:

```yaml
model:
  name: xxxs
  description: "0.5B parameter model"
  llama_cpp_binary: "/usr/bin/llama.cpp"
  llama_cpp_args:
    - "--slots"
    - "--model=/models/xxxs.bin"
    - "--host=127.0.0.1"
    - "--port=8088"
balancer:
  management_addr: "127.0.0.1:8085"
  reverseproxy_addr: "192.168.2.10:8080"
  statsd_prefix: "paddler.xxxs"
  statsd_addr: "127.0.0.1:8125"
  dashboard_enabled: true
  paddler_binary: "/usr/bin/paddler"
agents:
  - name: agent1
    host: "localhost" # For distributed setups, e.g., "192.168.1.100"
    external_llamacpp_addr: "127.0.0.1:8088"
    local_llamacpp_addr: "127.0.0.1:8088"
    management_addr: "127.0.0.1:8085"
    api_key: "" # Optional
```

**Notes**:

- `model.name` must match the filename (e.g., `xxxs` for `xxxs.yaml`).
- Ports increment per model (e.g., `8085`/`8088` for xxxs, `8086`/`8089` for xxs).
- `host: localhost` for your test setup; use IP/hostname for distributed setups.

#### 2. **Systemd Template Units**

Define static template units that read configs dynamically using `yq`.

**Failure Handler**:

```ini
# /etc/systemd/system/failure-handler@.service
[Unit]
Description=Failure handler for %i

[Service]
Type=oneshot
ExecStart=/usr/bin/logger -t paddler "Service %i failed"

# /etc/systemd/system/paddler@.service.d/10-failure.conf
[Unit]
OnFailure=failure-handler@%N.service

# /etc/systemd/system/paddler-agent@.service.d/10-failure.conf
[Unit]
OnFailure=failure-handler@%N.service

# /etc/systemd/system/llama-cpp@.service.d/10-failure.conf
[Unit]
OnFailure=failure-handler@%N.service

# Break recursive dependency
mkdir /etc/systemd/system/failure-handler@.service.d/
ln -s /dev/null /etc/systemd/system/failure-handler@.service.d/10-failure.conf
```

**Setup**:

```bash
sudo mkdir -p /etc/systemd/system/{paddler@.service.d,paddler-agent@.service.d,llama-cpp@.service.d,failure-handler@.service.d}
# Copy the above unit files
sudo systemctl daemon-reload
```

#### 3. **Paddler-Deploy CLI**

The CLI orchestrates deployment without generating service files, relying on template units.

**Dependencies** (`requirements.txt`):

```
pyyaml
paramiko
```

**Install**:

```bash
sudo apt install yq
pip install -r requirements.txt
```

**Usage**:

- Start xxxs: `sudo systemctl start paddler@xxxs`
- Enable xxxs on boot: `sudo systemctl enable paddler@xxxs`
- Deploy all: `sudo python3 paddler-deploy.py deploy`
- Stop xxs: `sudo python3 paddler-deploy.py stop --model xxs`
- Restart xxxs: `sudo python3 paddler-deploy.py restart --model xxxs`
- Add agent: Edit `xxxs.yaml`, then `sudo python3 paddler-deploy.py deploy --model xxxs`
- Remove agent: `sudo python3 paddler-deploy.py remove-agent --model xxxs --agent agent1`, then edit `xxxs.yaml`

#### 4. **Monitoring Integration**

Use the existing `paddler_manager.py` (from previous responses), updated to read `/etc/paddler/inventory.yaml`.

**`paddler_manager.py` (snippet)**:

```python
class PaddlerInventory:
    def __init__(self, inventory_file="/etc/paddler/inventory.yaml"):
        self.inventory_file = inventory_file
        self.instances = self.load_inventory()
```

**Docker Compose** (unchanged):

```yaml
version: "3.8"
services:
  statsd-exporter:
    image: prom/statsd-exporter
    ports:
      - "9102:9102"
      - "8125:8125/udp"
    command: --statsd.listen-udp=:8125 --web.listen-address=:9102

  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin

  paddler-exporter:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - /etc/paddler/inventory.yaml:/app/inventory.yaml
```

**`prometheus.yml`**:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: "statsd"
    static_configs:
      - targets: ["statsd-exporter:9102"]
  - job_name: "paddler-exporter"
    static_configs:
      - targets: ["paddler-exporter:8000"]
```

**`Dockerfile`**:

```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY paddler_manager.py inventory.yaml requirements.txt ./
RUN pip install -r requirements.txt
CMD ["python", "paddler_manager.py"]
```

#### 5. **Distributed Setup**

For Raspberry Pi clusters:

- Update `agents.host` in configs (e.g., `192.168.1.100`).
- Set up SSH keys for the `paddler` user:
  ```bash
  sudo -u paddler ssh-keygen -t rsa -b 4096 -f /home/paddler/.ssh/id_rsa
  sudo -u paddler ssh-copy-id paddler@192.168.1.100
  ```
- Ensure `paddler` user exists on remote nodes with `/usr/bin/paddler` and `/usr/bin/llama.cpp`.
- Test SSH: `sudo -u paddler ssh paddler@192.168.1.100`.

For your test setup, keep `host: localhost`.

#### 6. **Grafana Dashboard**

Configure as before:

- API metrics: `paddler_slots_idle{instance="xxxs"}`, `paddler_slots_processing{instance="xxxs"}`
- StatsD metrics: `paddler_xxxs_requests_buffered`
- Use templating for model selection.

### Setup Instructions

1. **Prepare System**:

   - Install binaries:
     ```bash
     sudo mv paddler /usr/bin/paddler
     sudo mv llama.cpp /usr/bin/llama.cpp
     sudo chmod +x /usr/bin/paddler /usr/bin/llama.cpp
     ```
   - Install `yq`: `sudo apt install yq`
   - Create directories:
     ```bash
     sudo mkdir -p /etc/paddler/models /var/lib/paddler
     sudo chown paddler:paddler /var/lib/paddler
     ```
   - Install Python dependencies:
     ```bash
     pip install -r requirements.txt
     ```
   - Copy template units and drop-ins to `/etc/systemd/system/`.
   - Run:
     ```bash
     sudo systemctl daemon-reload
     ```

2. **Create Model Configs**:

   - Create YAML files in `/etc/paddler/models/` (e.g., `xxxs.yaml`, `xxs.yaml`).
   - Ensure `model.name` matches the filename.
   - Update ports (e.g., `8085`/`8088` for xxxs, `8086`/`8089` for xxs).

3. **Deploy**:

   - Deploy all:
     ```bash
     sudo python3 paddler-deploy.py deploy
     ```
   - Or use systemd:
     ```bash
     sudo systemctl start paddler@xxxs paddler@xxs paddler@xs
     sudo systemctl enable paddler@xxxs paddler@xxs paddler@xs
     ```

4. **Start Monitoring**:

   ```bash
   docker-compose up -d
   ```

5. **Run Manager**:

   ```bash
   python3 paddler_manager.py
   ```

6. **Access Grafana**:
   - URL: `http://localhost:3000`
   - Login: `admin/admin`

### Additional Notes

- **Why No Service Generation**: The template units (`paddler@.service`, etc.) eliminate the need to generate service files, as they dynamically load the correct `/etc/paddler/models/<model>.yaml` based on `%i`. The `paddler-deploy` CLI simply enables/disables these instances and validates configs.
- **Agent Management**: Adding/removing agents requires editing the model’s YAML file and redeploying. If Paddler’s API supports dynamic agent registration in the future, we can extend `paddler-deploy` to use it.
- **Validation**: The CLI checks for config existence, binary paths, and basic port conflicts. Add more checks (e.g., network reachability) as needed.
- **Security**:
  - Restrict `management_addr` to trusted networks.
  - Secure SSH keys and Grafana access.
  - Use `iptables` or a firewall to limit port exposure.
- **Test Setup**: Since all services are local, `host: localhost` simplifies deployment. For Raspberry Pi clusters, test SSH and network connectivity first.
- **Extensibility**: If you add new models, create a new YAML file and run `paddler-deploy.py deploy --model <newmodel>`.

### Example Workflow

1. **Add a New Model (e.g., `xs`)**:

   - Create `/etc/paddler/models/xs.yaml`:
     ```yaml
     model:
       name: xs
       description: "3B parameter model"
       llama_cpp_binary: "/usr/bin/llama.cpp"
       llama_cpp_args:
         - "--slots"
         - "--model=/models/xs.bin"
         - "--host=127.0.0.1"
         - "--port=8090"
     balancer:
       management_addr: "127.0.0.1:8087"
       reverseproxy_addr: "192.168.2.10:8082"
       statsd_prefix: "paddler.xs"
       statsd_addr: "127.0.0.1:8125"
       dashboard_enabled: true
       paddler_binary: "/usr/bin/paddler"
     agents:
       - name: agent1
         host: "localhost"
         external_llamacpp_addr: "127.0.0.1:8090"
         local_llamacpp_addr: "127.0.0.1:8090"
         management_addr: "127.0.0.1:8087"
         api_key: ""
     ```
   - Deploy:
     ```bash
     sudo python3 paddler-deploy.py deploy --model xs
     ```

2. **Remove an Agent**:

   - Run:
     ```bash
     sudo python3 paddler-deploy.py remove-agent --model xxxs --agent agent1
     ```
   - Edit `/etc/paddler/models/xxxs.yaml` to remove the agent.
   - Redeploy:
     ```bash
     sudo python3 paddler-deploy.py deploy --model xxxs
     ```

3. **Check Status**:

   ```bash
   systemctl status paddler@xxxs
   systemctl status paddler-agent@xxxs-agent1
   systemctl status llama-cpp@xxxs
   ```

4. **View Metrics**:
   - Open Grafana: `http://localhost:3000`
   - Or use `paddler_manager.py`:
     ```bash
     python3 paddler_manager.py
     # Select "Query API" for xxxs
     ```
