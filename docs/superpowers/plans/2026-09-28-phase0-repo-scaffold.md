# Phase 0 + Start of Phase 1: Repo Scaffold, Guardrails, Local Compose Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get a working local development skeleton running: repo-wide lint/format/secret-scan guardrails, a minimal FastAPI backend with health/readiness endpoints, and a `docker compose up` that brings up MySQL + Redis + the API with the API's readiness check actually proving it can reach both.

**Architecture:** A single Python service package (`services/glassbox`) that will grow into the API and worker from `DESIGN.md` §6.2–6.3. This plan only builds the skeleton: app factory, health checks, and the Docker/Compose wiring. No business logic (RAG, chunking, caching, SSE) yet — that's the next plan, once this skeleton is verified working.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, aiomysql, redis-py (async client), pytest + pytest-asyncio + httpx, ruff (lint/format), pre-commit, detect-secrets, Docker Compose, MySQL 8, Redis (redis-stack-server image, since vector search is added in a later phase and this avoids an image swap).

## Global Constraints

- Python version: 3.12 (`DESIGN.md` §6.2).
- Database: MySQL 8.x (`DESIGN.md` §6.6). Cache/queue: Redis, using the `redis-stack-server` image so the vector-search module is present when later phases need it (`DESIGN.md` §6.5).
- No secrets committed to the repo, ever (`DESIGN.md` §10.6) — enforced here via a pre-commit secret scanner, not just a written rule.
- Repository layout follows `DESIGN.md` §16: service code under `services/glassbox/`, tests under `services/tests/`, a single `services/Dockerfile` shared by API and worker.
- Commit after each task, per `CLAUDE.md`'s commit-discipline instruction — each task below ends with its own commit.
- This plan explicitly does **not** touch AWS, Terraform, or the frontend. Those are blocked on the Milestone 0 checklist (`DESIGN-004-action-plan.md` §4) and DD1 Phase 3 respectively.

---

### Task 1: Repo-wide guardrails — lint, format, secret scanning

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `.pre-commit-config.yaml`
- Create: `.secrets.baseline`

**Interfaces:**
- Produces: a `pyproject.toml` with a `[tool.ruff]` section that later tasks' Python code must pass (`ruff check .` and `ruff format --check .`).

- [ ] **Step 1: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.ruff_cache/

# Node (for later frontend work)
node_modules/
dist/
.expo/

# Env
.env
.env.*
!.env.example

# OS / editor
.DS_Store
.vscode/
.idea/
```

- [ ] **Step 2: Create `pyproject.toml` with ruff config**

```toml
[project]
name = "glassbox"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 3: Install `detect-secrets` and generate a baseline**

```bash
pip install detect-secrets==1.5.0
detect-secrets scan > .secrets.baseline
```

Expected: `.secrets.baseline` is created and contains `"results": {}` (empty repo, nothing to flag yet).

- [ ] **Step 4: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ["--baseline", ".secrets.baseline"]
```

If any pinned `rev` is no longer current when you run this, `pre-commit autoupdate` will bump them — that's expected and fine.

- [ ] **Step 5: Install pre-commit and register the git hook**

```bash
pip install pre-commit==3.8.0
pre-commit install
```

- [ ] **Step 6: Run pre-commit against the whole repo**

Run: `pre-commit run --all-files`
Expected: all hooks report `Passed` (there's no Python code yet, so ruff has nothing to flag; detect-secrets checks the doc files and finds no matches).

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml .pre-commit-config.yaml .secrets.baseline
git commit -m "chore: add lint, format, and secret-scanning guardrails"
```

---

### Task 2: FastAPI backend skeleton with `/healthz` and `/readyz`

**Files:**
- Create: `services/__init__.py`
- Create: `services/requirements.txt`
- Create: `services/requirements-dev.txt`
- Create: `services/glassbox/__init__.py`
- Create: `services/glassbox/api/__init__.py`
- Create: `services/glassbox/api/db.py`
- Create: `services/glassbox/api/cache.py`
- Create: `services/glassbox/api/main.py`
- Create: `services/tests/__init__.py`
- Create: `services/tests/test_health.py`
- Create: `services/Dockerfile`
- Modify: `pyproject.toml` (add `[tool.pytest.ini_options]`)

**Interfaces:**
- Produces: `services.glassbox.api.db.ping_mysql() -> bool` (async), `services.glassbox.api.cache.ping_redis() -> bool` (async), `services.glassbox.api.main.app` (FastAPI instance) exposing `GET /healthz` → `{"status": "ok"}` and `GET /readyz` → `{"mysql": bool, "redis": bool, "ready": bool}` with HTTP 200 when `ready` else 503.
- Consumes: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `REDIS_URL` environment variables (no defaults — fail loudly if unset, since Task 3's Compose file always sets them).

- [ ] **Step 1: Create the Python package skeleton (empty `__init__.py` files)**

```bash
mkdir -p services/glassbox/api services/tests
touch services/__init__.py services/glassbox/__init__.py \
      services/glassbox/api/__init__.py services/tests/__init__.py
```

- [ ] **Step 2: Add pytest config to `pyproject.toml`**

Add this section to the existing `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["services/tests"]
```

- [ ] **Step 3: Create `services/requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
aiomysql==0.2.0
redis==5.0.8
```

- [ ] **Step 4: Create `services/requirements-dev.txt`**

```
-r requirements.txt
pytest==8.3.3
pytest-asyncio==0.24.0
httpx==0.27.2
```

- [ ] **Step 5: Set up a virtualenv and install dependencies**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r services/requirements-dev.txt
```

- [ ] **Step 6: Write the failing test for `/healthz`**

Create `services/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from services.glassbox.api.main import app

client = TestClient(app)


def test_healthz_returns_ok():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 7: Run the test and verify it fails**

Run: `pytest services/tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.glassbox.api.main'`

- [ ] **Step 8: Create `services/glassbox/api/db.py`**

```python
import os

import aiomysql


async def ping_mysql() -> bool:
    try:
        conn = await aiomysql.connect(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ["MYSQL_DATABASE"],
            connect_timeout=3,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        finally:
            conn.close()
        return True
    except Exception:
        return False
```

- [ ] **Step 9: Create `services/glassbox/api/cache.py`**

```python
import os

import redis.asyncio as redis


async def ping_redis() -> bool:
    try:
        client = redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3)
        try:
            pong = await client.ping()
        finally:
            await client.aclose()
        return bool(pong)
    except Exception:
        return False
```

- [ ] **Step 10: Create `services/glassbox/api/main.py`**

```python
from fastapi import FastAPI, Response

from services.glassbox.api.cache import ping_redis
from services.glassbox.api.db import ping_mysql

app = FastAPI(title="glassbox-api")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, bool]:
    mysql_ok = await ping_mysql()
    redis_ok = await ping_redis()
    ready = mysql_ok and redis_ok
    if not ready:
        response.status_code = 503
    return {"mysql": mysql_ok, "redis": redis_ok, "ready": ready}
```

- [ ] **Step 11: Run the test and verify it passes**

Run: `pytest services/tests/test_health.py -v`
Expected: PASS

- [ ] **Step 12: Write failing tests for `/readyz`**

Add to `services/tests/test_health.py`:

```python
from unittest.mock import AsyncMock, patch


def test_readyz_returns_200_when_dependencies_up():
    with (
        patch("services.glassbox.api.main.ping_mysql", new=AsyncMock(return_value=True)),
        patch("services.glassbox.api.main.ping_redis", new=AsyncMock(return_value=True)),
    ):
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"mysql": True, "redis": True, "ready": True}


def test_readyz_returns_503_when_a_dependency_is_down():
    with (
        patch("services.glassbox.api.main.ping_mysql", new=AsyncMock(return_value=True)),
        patch("services.glassbox.api.main.ping_redis", new=AsyncMock(return_value=False)),
    ):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"mysql": True, "redis": False, "ready": False}
```

Move the `from unittest.mock import AsyncMock, patch` line to the top of the file with the other imports.

- [ ] **Step 13: Run the tests and verify the new ones pass**

Run: `pytest services/tests/test_health.py -v`
Expected: all 3 tests PASS (the mocks replace the real `ping_mysql`/`ping_redis`, so this test needs no real MySQL/Redis running).

- [ ] **Step 14: Create `services/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY services/requirements.txt services/requirements.txt
RUN pip install --no-cache-dir -r services/requirements.txt

COPY services/glassbox services/glassbox

EXPOSE 8000
CMD ["uvicorn", "services.glassbox.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 15: Run ruff to confirm the new code is clean**

Run: `ruff check . && ruff format --check .`
Expected: no errors. If ruff reports formatting issues, run `ruff format .` and re-check.

- [ ] **Step 16: Commit**

```bash
git add services/ pyproject.toml
git commit -m "feat: add FastAPI backend skeleton with health and readiness checks"
```

---

### Task 3: Docker Compose skeleton (MySQL + Redis + API)

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

**Interfaces:**
- Consumes: `services/Dockerfile` and the `services/` build context from Task 2.
- Produces: a running stack reachable at `http://localhost:8000`.

- [ ] **Step 1: Create `.env.example`**

```
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_USER=glassbox
MYSQL_PASSWORD=glassbox
MYSQL_DATABASE=glassbox
REDIS_URL=redis://redis:6379/0
```

This documents the variables `docker-compose.yml` sets directly for now; it becomes the real `.env` file once more services (and secrets like Bedrock credentials) are added in later phases.

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: root
      MYSQL_DATABASE: glassbox
      MYSQL_USER: glassbox
      MYSQL_PASSWORD: glassbox
    ports:
      - "3306:3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-proot"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis/redis-stack-server:7.2.0-v11
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 10

  api:
    build:
      context: .
      dockerfile: services/Dockerfile
    environment:
      MYSQL_HOST: mysql
      MYSQL_PORT: "3306"
      MYSQL_USER: glassbox
      MYSQL_PASSWORD: glassbox
      MYSQL_DATABASE: glassbox
      REDIS_URL: redis://redis:6379/0
    ports:
      - "8000:8000"
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
```

- [ ] **Step 3: Build and start the stack**

```bash
docker compose up -d --build
```

- [ ] **Step 4: Wait for all services to be healthy**

Run: `docker compose ps`
Expected: `mysql`, `redis`, and `api` all show `running`/`healthy` (may take ~20-30s for MySQL's first boot).

- [ ] **Step 5: Verify `/healthz`**

Run: `curl -s http://localhost:8000/healthz`
Expected: `{"status":"ok"}`

- [ ] **Step 6: Verify `/readyz` reaches both real dependencies**

Run: `curl -s -w '\n%{http_code}\n' http://localhost:8000/readyz`
Expected: `{"mysql":true,"redis":true,"ready":true}` followed by `200` — this is the real proof the Compose network wiring works, not just that the API process starts.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add docker-compose skeleton for local MySQL, Redis, and API"
```

---

## Self-Review Notes

- **Spec coverage:** This plan covers DD1 §17 Phase 0 (guardrails) minus the Terraform/GitHub OIDC bootstrap (blocked on the AWS account decision in `DESIGN-004-action-plan.md` §4, out of scope here by design) and the very start of Phase 1 (Docker Compose with MySQL + Redis, per DD1 §17). Schema/migrations, ingestion, the full SSE contract, and real Bedrock providers are **not** in this plan — they're the next plan once this skeleton is verified.
- **Placeholder scan:** No TBDs; every step has literal file contents or a literal command with an expected result.
- **Type/interface consistency:** `ping_mysql`/`ping_redis` are both `async def ... -> bool`, imported by name into `main.py`, and the test file patches them at `services.glassbox.api.main.ping_mysql`/`ping_redis` (where they're used, not where they're defined) — correct per how `main.py` imports them.
- **Scope check:** Single subsystem (backend skeleton + local Compose), no decomposition needed.
