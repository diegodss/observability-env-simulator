## How to run

The project is split into two repositories that talk to each other over the `observability-sim` network:

- **observability-env-simulator**: simulated applications (technology-one-sm, edw, appevents workers).
- **observability-platform**: Prometheus, Alertmanager, Grafana and KeepHQ.

Each one can run in two ways, your choice: **Docker Compose** (simpler) or **Docker Swarm**. The configuration files (`prometheus/`, `alertmanager/`, `grafana/`, `keep/`) are the same in both modes.

> ⚠️ **Use one mode at a time.** In Compose, the `observability-sim` network is a *bridge*; in Swarm it is an *overlay*. Both repositories must run in the same mode.

---

### Mode 1: Docker Compose

No setup needed: every value has a default. To override anything, copy `.env.example` to `.env`.

```bash
# 1. Simulator
cd observability-env-simulator
docker compose up --build -d

# 2. Platform
cd ../observability-platform
docker compose up -d
```

> ⚠️ **Order matters: start the simulator first and wait until it is healthy** (`python3 scripts/status.py`) before starting the platform. If the platform starts first, Prometheus marks the targets as DOWN, alerts fire and Keep opens Jira tickets for services that simply haven't started yet.

Whichever starts first creates the network, and the second one prints a harmless warning that the network "was not created for project ...".

To take only `edw` down and simulate an outage:

```bash
docker compose stop edw     # take it down
docker compose start edw    # bring it back
```

---

### Mode 2: Docker Swarm

The files live in the `docker-swarm/` folder of each repository. An active Swarm is required (`docker swarm init`).

#### Prerequisites (one time only)

```bash
# Overlay network shared by both stacks
docker network create -d overlay --attachable observability-sim

# Overlay network for Traefik (reverse proxy). Required by the platform,
# because its stack references it as an external network.
docker network create -d overlay --attachable traefik-public

# Label the node where the services will run (get its name with: docker node ls)
docker node update --label-add obs=true <NODE_NAME>
```

Services are pinned to that node because they use bind mounts and local images. With more than one node, you would need a registry for the images and shared storage (for example NFS) for the configuration files.

#### Environment variables

`docker stack deploy` **does not read the `.env` file automatically**. Defaults work out of the box, but to override values, export them before deploying:

```bash
set -a; source ../.env; set +a
```

On the platform, `JIRA_API_TOKEN` has no default and is required for KeepHQ to create tickets:

```bash
export JIRA_API_TOKEN=...
```

> ⚠️ **Same rule: deploy the simulator first** and wait until its services are ready (`docker stack services simulator`, all at `1/1`) before starting the platform. Otherwise alerts fire and Keep opens Jira tickets.

#### Simulator

Swarm does not build images. Build them first, from the repository root:

```bash
docker build -t observability-env-simulator/technology-one-sm:local ./app-technology-one-sm
docker build -t observability-env-simulator/edw:local ./app-edw
docker build -t observability-env-simulator/appevents-worker:local ./integrations

cd docker-swarm
docker stack deploy -c simulator-stack.yml simulator
```

#### Platform

```bash
cd docker-swarm
docker stack deploy -c observability-stack.yml observability
```

#### Access by hostname (Traefik + dnsmasq)

On Swarm, the platform **does not publish ports** for Keep, Grafana, Prometheus and Alertmanager. They are reached by hostname through Traefik, which runs in a **separate stack** (`traefik-stack.yml`) so it can also serve any other stacks you create.

| Hostname                    | Service            |
| --------------------------- | ------------------ |
| `keep.swarm.test`         | Keep (frontend)    |
| `keep-api.swarm.test`     | Keep (backend/API) |
| `grafana.swarm.test`      | Grafana            |
| `prometheus.swarm.test`   | Prometheus         |
| `alertmanager.swarm.test` | Alertmanager       |
| `traefik.swarm.test`      | Traefik dashboard  |

**1. Deploy Traefik** (once; it then serves every stack):

```bash
cd docker-swarm
docker stack deploy -c traefik-stack.yml traefik
```

**2. Set up DNS.** Traefik listens on port 80 of the node it runs on (a manager). Make `*.swarm.test` resolve to that node's IP. With dnsmasq, one line covers every subdomain, current and future:

```
address=/swarm.test/<NODE_IP>
```

Then restart dnsmasq and point your machines' (or your router's) DNS to it. For single-machine use you can edit `/etc/hosts` instead, but it does not support wildcards, so list each hostname:

```
<NODE_IP> keep.swarm.test keep-api.swarm.test grafana.swarm.test prometheus.swarm.test alertmanager.swarm.test traefik.swarm.test
```

> Avoid the `.local` suffix: it is reserved for mDNS and often causes slow or intermittent name resolution.

**3. Test:**

```bash
curl -I http://keep.swarm.test
curl -I http://grafana.swarm.test
```

Things to keep in mind:

- Keep's URLs (`NEXTAUTH_URL`, `KEEP_EXTERNAL_URL`, `NEXT_PUBLIC_*`) already point to the hostnames above. `KEEP_EXTERNAL_URL` is what shows up in the links of Jira tickets.
- Keep's frontend talks to the backend from the browser, so `keep-api.swarm.test` must also resolve on the machines that open the UI.
- To expose another stack, put its service on the `traefik-public` network as well and add labels under `deploy.labels`, using a router name that is **unique** across all stacks (example in the header of `traefik-stack.yml`).
- Traefik mounts the Docker socket, which gives broad access to the host. That is acceptable for a lab.
- Plain Compose is not affected: it keeps publishing host ports as before.

#### Operations

```bash
docker stack services simulator            # service status
docker stack services observability
docker service logs -f simulator_edw       # logs of a single service

# Equivalent to "compose stop/start edw":
docker service scale simulator_edw=0
docker service scale simulator_edw=1

# Remove
docker stack rm simulator
docker stack rm observability
```

Since Swarm has no `depends_on`, some services (Keep backend, applications and databases) may restart a few times on first start until the database becomes healthy. This is expected and resolves on its own.

---

### Switching modes

Bring everything down before switching and remove the network, because the name is the same but the type (bridge or overlay) is different:

```bash
# Leaving Compose
docker compose down            # in each repository

# Leaving Swarm
docker stack rm simulator observability

# Then, in either case
docker network rm observability-sim
```

Then follow the steps for the mode you chose.
