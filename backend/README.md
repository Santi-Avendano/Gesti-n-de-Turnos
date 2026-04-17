# Turnero — Backend

API para gestión de turnos con grilla de horarios predefinida, multi-tenant por organización, autenticación JWT (RS256 + refresh tokens con rotación y detección de reuse), construida con **FastAPI + PostgreSQL + SQLAlchemy 2.0 async**.

> Especificación funcional: [`../REQUIREMENTS.md`](../REQUIREMENTS.md)
> Plan de implementación aprobado: [`~/.claude/plans/eres-un-desarrollador-backend-cheerful-lantern.md`](~/.claude/plans/eres-un-desarrollador-backend-cheerful-lantern.md)

---

## Estado actual

| Fase | Descripción | Estado |
|---|---|---|
| 0 | Bootstrap (pyproject, docker-compose, conftest, smoke test) | ✅ |
| 1 | Slot computation engine (función pura + tests DST) | ✅ |
| 2 | Modelos SQLAlchemy + migración inicial Alembic (con partial unique index) | ✅ |
| 3 | Auth flow (RS256 + refresh rotation + reuse detection) | 🚧 endpoints listos, faltan tests integration |
| 4 | Concurrencia RF-4.2 (booking_service + tests paralelos) | ⏳ |
| 5 | Availability CRUD (grid + exceptions) | ⏳ |
| 6 | Slots + Bookings endpoints (lifecycle completo) | ⏳ |
| 7 | Health endpoints + main app + verificación E2E | ✅ health · ⏳ E2E |

---

## Stack

| Componente | Elección | Por qué |
|---|---|---|
| Lenguaje | **Python 3.12+** | `zoneinfo`, type hints modernos |
| Framework | **FastAPI ~0.115** | OpenAPI auto, async-first |
| ORM | **SQLAlchemy 2.0 async + asyncpg** | Estándar moderno con FastAPI |
| Migraciones | **Alembic** (async-aware) | De facto con SQLAlchemy |
| Validación | **Pydantic v2 + pydantic-settings** | Integrado en FastAPI |
| JWT | **PyJWT 2.x** (NO python-jose) | python-jose sin mantener (CVEs de algorithm confusion) |
| Algoritmo JWT | **RS256** (NO HS256) | Validable desde otros servicios sin compartir secret |
| Passwords | **`bcrypt` directo** (NO passlib) | passlib estancado desde 2020 |
| Package manager | **uv** | Lock reproducible, rápido |
| Tests | **pytest + pytest-asyncio + httpx + testcontainers-postgres** | Postgres real, no mocks |
| Lint/format/types | **ruff + mypy** | — |
| DB local | **Docker Compose (Postgres 16)** | — |

---

## Arquitectura

### Estructura del proyecto

```
backend/
├── pyproject.toml           # uv + deps + ruff/mypy/pytest config
├── docker-compose.yml       # Postgres 16
├── env.example              # ⚠️ renombrar a .env (el sandbox bloquea .env*)
├── alembic.ini
├── alembic/
│   ├── env.py               # async-aware
│   └── versions/
│       └── 0001_initial.py  # schema + partial unique index para RF-4.2
├── app/
│   ├── main.py              # FastAPI factory + lifespan + CORS + handlers
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings)
│   │   ├── security.py      # JWT RS256, bcrypt, refresh hashing
│   │   ├── deps.py          # CurrentPrincipal, get_current_principal, require_admin
│   │   ├── exceptions.py    # AppError + handlers
│   │   └── time.py          # local_to_utc_or_none (DST-safe)
│   ├── db/
│   │   ├── base.py          # Declarative Base + naming convention
│   │   └── session.py       # async engine + sessionmaker (lazy)
│   ├── models/              # SQLAlchemy 2.0 (Organization, User, RefreshToken,
│   │                        #                  AvailabilityRule, Exception_, Booking)
│   ├── schemas/             # Pydantic (auth listo)
│   ├── services/            # auth_service, slot_service (resto pending)
│   └── api/v1/              # auth ✅, health ✅, resto stubs
└── tests/
    ├── conftest.py          # testcontainers Postgres + transactional savepoint
    ├── unit/
    │   └── test_slot_service.py    # 12 tests, incluye DST
    └── integration/
        └── test_smoke.py           # liveness + readiness
```

### Decisiones de arquitectura

| Decisión | Implementación |
|---|---|
| **Multi-tenant por Admin** | `organization_id` FK en TODA tabla (`users`, `availability_rules`, `exceptions`, `bookings`, `refresh_tokens` vía user). Scoping desde claim JWT. |
| **Timezone por organización** | `organizations.timezone` (string TZ, ej. `America/Argentina/Buenos_Aires`). Grilla almacenada como `(day_of_week, start_local_time, end_local_time)`. Conversión a UTC al computar slots. |
| **Slots on-demand (RF-2.4)** | NO existe tabla `slots`. Función pura `compute_available_slots()` calcula = grilla − bookings activos − exceptions. |
| **Concurrencia RF-4.2** | Partial unique index: `UNIQUE (organization_id, start_at_utc) WHERE status='active'`. INSERT atómico, IntegrityError ⇒ 409. |
| **Access + refresh tokens** | RS256 access (15 min), refresh (7 días) hasheado en DB con SHA-256. Rotación obligatoria + **detección de reuse**: presentar un refresh ya revocado revoca toda la familia. |
| **Discovery de org** | `org_slug` explícito en register/login. Sin subdominios. |
| **Auditoría (RF-4.4)** | `bookings.cancelled_by_user_id` + `cancelled_at`. |

### Modelo de datos

```
organizations (id, name, slug UNIQUE, timezone, slot_duration_minutes, booking_horizon_days, min_lead_minutes, created_at)
users (id, organization_id, email, password_hash, role enum, created_at)
  └── UNIQUE (organization_id, email)
refresh_tokens (id, user_id, token_family_id UUID, token_hash sha256, expires_at, revoked_at, created_at)
availability_rules (id, organization_id, day_of_week 0-6, start_local_time, end_local_time)
exceptions (id, organization_id, start_at_utc, end_at_utc, reason, kind enum)
bookings (id, organization_id, user_id, start_at_utc, end_at_utc, status enum,
          created_at, cancelled_at, cancelled_by_user_id)
  └── UNIQUE INDEX uniq_active_booking_slot ON (organization_id, start_at_utc) WHERE status='active'
```

---

## Endpoints implementados

### Auth (`/api/v1/auth`)

| Método | Path | Auth | Descripción |
|---|---|---|---|
| POST | `/admin/register` | público | Crea Org + usuario Admin atómicamente |
| POST | `/register` | público | Registra User común en una org existente (necesita `org_slug`) |
| POST | `/login` | público | `{org_slug, email, password}` → `{access_token, refresh_token, expires_in}` |
| POST | `/refresh` | refresh token | Rota tokens. **Detecta reuse → revoca familia** |
| POST | `/logout` | refresh token | Revoca el refresh token actual (no leak en token desconocido) |
| GET | `/me` | access token | Devuelve datos del usuario + org |

### Health (`/api/v1`)

| Método | Path | Descripción |
|---|---|---|
| GET | `/health` | Liveness (siempre 200 si proceso vivo) |
| GET | `/health/ready` | Readiness — verifica conexión a DB con `SELECT 1` |

### Pendientes (stubs creados, sin implementación)
- `/api/v1/orgs/me` (GET, PATCH)
- `/api/v1/availability/grid`, `/api/v1/availability/exceptions/...`
- `/api/v1/slots`, `/api/v1/admin/calendar`
- `/api/v1/bookings/...`

---

## Setup local

### 1. Requisitos

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker + Docker Compose

### 2. Variables de entorno

```bash
cp env.example .env   # ⚠️ el sandbox del agente no puede crear `.env*`, hacelo a mano
```

Luego, generá el par de claves RSA para el JWT y completalo en `.env`:

```bash
openssl genpkey -algorithm RSA -out private.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -in private.pem -pubout -out public.pem

echo "JWT_PRIVATE_KEY_PEM_BASE64=$(base64 -w0 private.pem)" >> .env
echo "JWT_PUBLIC_KEY_PEM_BASE64=$(base64 -w0 public.pem)" >> .env
rm private.pem public.pem
```

### 3. Levantar Postgres

```bash
docker compose up -d postgres
```

### 4. Instalar dependencias y migrar

```bash
uv sync
uv run alembic upgrade head
```

### 5. Levantar el server

```bash
uv run uvicorn app.main:app --reload
```

OpenAPI interactivo: <http://localhost:8000/docs>

---

## Tests

```bash
# Todo
uv run pytest

# Solo el núcleo del cómputo de slots (no requiere DB)
uv run pytest tests/unit/test_slot_service.py

# Solo smoke (requiere Postgres testcontainer corriendo + Docker disponible)
uv run pytest -m smoke

# Lint + types
uv run ruff check .
uv run mypy app
```

### Cobertura actual de tests
- ✅ **Slot computation (12 tests, unit, sin DB)**: simple weekday grid, fin de semana, lunch break, slots no divisibles, exception full-day, exception partial-overlap, booking activo, slots pasados, `min_lead_minutes`, **DST spring-forward (NY)**, **DST fall-back fold=0**, Argentina (sin DST), rango vacío, frozen `Slot`.
- ✅ **Smoke (2 tests, integration)**: `/health` y `/health/ready`.
- ⏳ **Auth flow** (próximo): register → login → /me → refresh → logout → reuse detection.
- ⏳ **Tenant isolation**: Org A no ve datos de Org B.
- ⏳ **Concurrencia RF-4.2**: 10 tasks paralelos → exactamente 1×201 + 9×409.

---

## Smoke manual (curl/httpie)

```bash
# 1. Crear org + admin
http POST :8000/api/v1/auth/admin/register \
  org_name="Demo" org_slug="demo" timezone="America/Argentina/Buenos_Aires" \
  email="admin@demo.com" password="secret123"

# 2. Login
http POST :8000/api/v1/auth/login \
  org_slug="demo" email="admin@demo.com" password="secret123"
# → guardar access_token

# 3. Verificar identidad
TOKEN="<access_token>"
http GET :8000/api/v1/auth/me Authorization:"Bearer $TOKEN"
```

(Endpoints de availability/slots/bookings aún no implementados.)

---

## Decisiones tomadas sin pregunta (revisable)

| Tema | Decisión MVP |
|---|---|
| Cambio de TZ de la org | **BLOQUEADO** vía PATCH (rompería bookings existentes) |
| Cambio de grilla con bookings existentes | **PERMITIDO** — bookings son contratos confirmados |
| Email verification on signup | **NO** en MVP |
| Rate limiting | **NO** en MVP — delegado a infra |
| Password policy | min 8 chars, sin reglas de complejidad |
| Notificación a User en cancel admin (RF-4.4) | Solo persiste `cancelled_by_user_id`; sin email |
| Soft-delete | NO — hard delete con cascada por FK |
| Audit log | Solo campos de cancelación; tabla dedicada queda como future work |
| Cache de slots | NO — solo si RNF-3 falla en carga real |

---

## Out of scope (explícito)
- Frontend
- Email/notificaciones
- Rate limiting
- Soft-delete y GDPR export
- Audit log table dedicada
- Métricas/observabilidad (Prometheus, OpenTelemetry)
- CI/CD (GitHub Actions)

---

## Próximos pasos
1. **Cerrar Phase 3**: `tests/integration/test_auth_flow.py` + `test_tenant_isolation.py`.
2. **Phase 4**: `booking_service.py` con mapeo `IntegrityError → 409` + test concurrente con asyncio.
3. **Phase 5**: Availability CRUD endpoints.
4. **Phase 6**: Slots + Bookings endpoints (lifecycle completo).
5. **Phase 7**: Verificación E2E.
