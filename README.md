# EDI Middleware — Let's Try Foods

Custom EDI middleware that receives Purchase Orders from ~15 retail platforms (Blinkit, Zepto, Swiggy, BigBasket, Amazon, etc.), normalizes them to a canonical EDI 850 schema, and pushes Sales Orders into SAP Business One via Service Layer.

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ |
| Node | 18+ |
| Docker + Docker Compose | v2+ |
| PostgreSQL | 15 (via Docker) |
| Redis | 7 (via Docker) |

---

## Quick Start (Docker)

```bash
# 1. Clone and enter the repo
cd "EDI integration"

# 2. Copy environment file and fill in secrets
cp .env.example .env
# Edit .env — at minimum set DATABASE_URL, REDIS_URL, B1_*, BLINKIT_*, ZEPTO_*

# 3. Start backend services (postgres, redis, api, workers, scheduler)
docker compose up

# 4. Run DB migrations (first time only)
docker compose exec api alembic upgrade head

# 5. (Optional) Run with frontend dev server
docker compose --profile dev up
```

API docs: http://localhost:8000/docs  
Health check: http://localhost:8000/health

---

## Local Development (without Docker)

### Backend

```bash
# Create virtualenv and install deps
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Copy and configure env
cp .env.example .env

# Start PostgreSQL and Redis (Docker)
docker compose up postgres redis -d

# Run migrations
alembic upgrade head

# Start API server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```

---

## Running Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Roll back one migration
alembic downgrade -1

# Create a new migration (auto-detect from models)
alembic revision --autogenerate -m "describe what changed"
```

---

## Project Structure

```
EDI integration/
├── CLAUDE.md               ← Architecture spec and phase plan (authoritative)
├── PLAYBOOK.md             ← Business rules (authoritative)
├── README.md               ← This file
├── pyproject.toml          ← Python dependencies
├── Dockerfile              ← Backend multi-stage image
├── docker-compose.yml      ← All services
├── .env.example            ← Config template (copy to .env)
├── alembic/                ← DB migrations
│   └── versions/
├── app/                    ← FastAPI backend
│   ├── main.py             ← Entry point
│   ├── config.py           ← pydantic-settings
│   ├── db.py               ← SQLAlchemy engines
│   ├── models/             ← ORM models (added Phase 1+)
│   ├── schemas/            ← Pydantic schemas
│   ├── adapters/           ← Partner ingest adapters
│   ├── parsers/            ← Raw → canonical parsers
│   ├── validators/         ← Business rule validators
│   ├── mappers/            ← canonical ↔ B1 transforms
│   ├── sap_b1/             ← SAP Service Layer client
│   ├── workflows/          ← End-to-end orchestration
│   ├── workers/            ← RQ jobs + APScheduler
│   ├── api/routes/         ← FastAPI route handlers
│   └── utils/              ← Money, GST, time helpers
├── frontend/               ← React + Vite SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── router.tsx
│   │   ├── lib/            ← axios client, TanStack Query config
│   │   ├── features/       ← Feature-first component folders
│   │   └── components/ui/  ← shadcn/ui primitives
│   └── Dockerfile
├── tests/                  ← pytest tests
├── scripts/                ← One-off utility scripts
├── docs/                   ← API notes, legacy reference
└── _archive/               ← Read-only legacy code (do not modify)
    ├── backend_old/
    └── frontend_old/
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 + FastAPI + SQLAlchemy 2 + Alembic |
| Database | PostgreSQL 15 |
| Queue | Redis 7 + RQ |
| Scheduler | APScheduler |
| Frontend | React 18 + Vite + TypeScript + Tailwind CSS v4 |
| UI components | shadcn/ui (Radix + Tailwind) |
| Server state | TanStack Query v5 |
| Forms | React Hook Form + Zod |
| ERP | SAP Business One (Service Layer REST) |

See [CLAUDE.md](CLAUDE.md) Section 2 for the complete tech stack rationale and the full list of forbidden libraries.

---

## SAP Business One Setup

Before Phase 6, complete the B1 prerequisites documented in [CLAUDE.md](CLAUDE.md) Section 7:
- Create `EDI_BOT` API user with appropriate permissions.
- Add UDFs on `ORDR` and `RDR1` tables.
- Create Business Partner records for each retailer.
- Verify India localization is enabled.

---

## Partner Credentials

See [docs/legacy-api-notes.md](docs/legacy-api-notes.md) for full Blinkit and Zepto API documentation including:
- Auth flows (headers, IP whitelisting)
- Inbound PO payload shapes
- ASN submission formats
- Known quirks and production bug history
