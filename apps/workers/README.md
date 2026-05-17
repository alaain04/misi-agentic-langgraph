# ##REPOSITORY_NAME##

##REPOSITORY_DESCRIPTION##

## 📦 Requisitos

- Python 3.12+
- Docker & Docker Compose
- `make`

---

## 🚀 Inicio rápido

### 1. Clonar y configurar

```bash
git clone <url-del-repo>
cd ##REPOSITORY_NAME##

cp .env.sample .env
# Completar .env con los valores del proyecto
```

### 2. Configurar el entorno local

```bash
make setup
```

Este comando:
- Verifica que Python 3.12+ esté disponible
- Crea el entorno virtual `venv/`
- Instala todas las dependencias de `requirements-dev.txt`
- Registra los hooks de pre-commit y commit-msg

> Para empezar de cero: `make restart-setup`

### 3. Levantar los servicios de infraestructura

```bash
make docker-up
# Levanta: PostgreSQL, Redis, LocalStack (SNS/SQS/S3), NATS
```

> Esperá unos segundos hasta que todos los servicios estén healthy antes de continuar. Podés verificarlo con `docker compose ps`.

> Para correr todo en Docker (sin `make dev`): `docker compose --profile api up -d`

### 4. Ejecutar las migraciones

```bash
make migrate
```

### 5. Iniciar la API

```bash
# Desarrollo (hot-reload)
make dev

# Producción
make run
```

La API queda disponible en `http://localhost:8000`
Documentación interactiva: `http://localhost:8000/docs` (solo cuando `DEBUG=true`)

---

## 🏗️ Arquitectura

```
Capa de Dominio (core)
    └── domain/ports/        ← Contratos abstractos (interfaces)
    └── domain/models/       ← Entidades y value objects

Capa de Adaptadores (infraestructura)
    └── adapters/cache/      ← Redis
    └── adapters/db/         ← SQLAlchemy (PostgreSQL)
    └── adapters/messaging/  ← AWS SNS+SQS (primario), NATS (alternativo)
    └── adapters/storage/    ← Filesystem local (intercambiable por S3/GCS)

Capa de Aplicación
    └── services/application_services/  ← Orquestación, sin reglas de negocio

Capa de API (adaptador entrante)
    └── api/routers/         ← Handlers de rutas FastAPI
    └── api/dependencies.py  ← Inyección de dependencias
    └── api/schemas.py       ← Modelos Pydantic de request/response
```

El **dominio** no tiene ninguna dependencia de FastAPI, SQLAlchemy, Redis ni AWS — solo depende de los puertos abstractos definidos en `domain/ports/`.

---

## 🛠️ Stack tecnológico

| Área              | Tecnología                            |
|-------------------|---------------------------------------|
| Framework         | FastAPI + Uvicorn                     |
| Base de datos     | PostgreSQL vía SQLAlchemy (async)     |
| Migraciones       | Alembic                               |
| Caché             | Redis                                 |
| Mensajería        | AWS SNS + SQS (LocalStack para dev)   |
| Mensajería alt.   | NATS                                  |
| Storage           | Filesystem local (intercambiable S3)  |
| Validación        | Pydantic v2                           |
| Linting           | Ruff                                  |
| Tipado            | mypy (strict)                         |
| Testing           | pytest + pytest-asyncio               |
| SAST              | bandit                                |
| CVEs dependencias | pip-audit                             |
| Secrets           | detect-secrets (local), gitleaks (CI) |
| Contenedores      | Docker + docker-compose               |

---

## 🗂️ Estructura del proyecto

```
.
├── api/                        # Adaptador HTTP entrante
│   ├── routers/                # Handlers por dominio
│   ├── dependencies.py         # Wiring de DI (adaptadores → puertos)
│   └── schemas.py              # Schemas Pydantic de request/response
│
├── domain/                     # Lógica de negocio — sin dependencias de framework
│   ├── models/                 # Entidades y value objects
│   └── ports/                  # Interfaces abstractas
│       ├── cache_port.py
│       ├── repository_port.py
│       ├── unit_of_work_port.py
│       ├── messaging_port.py
│       └── storage_port.py
│
├── adapters/                   # Implementaciones concretas de los puertos
│   ├── cache/
│   │   └── redis_adapter.py
│   ├── db/sqlalchemy/
│   │   ├── models.py           # Modelos ORM
│   │   ├── sqlalchemy_repository.py
│   │   ├── sqlalchemy_unit_of_work.py
│   │   └── migrations/         # Archivos de migración Alembic
│   ├── messaging/
│   │   ├── sns_sqs_adapter.py  # Primario
│   │   └── nats_adapter.py     # Alternativo
│   └── storage/
│       └── local_storage_adapter.py
│
├── services/
│   └── application_services/   # Capa de orquestación
│
├── config/
│   └── settings.py             # Configuración con pydantic-settings
│
├── scripts/
│   └── localstack-init.sh      # Bootstrap de SNS/SQS/S3 en LocalStack
│
├── main.py                     # Factory de la app FastAPI + lifespan
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml              # Configuración de Ruff, mypy, pytest y bandit
├── sonar-project.properties    # Configuración de análisis SonarQube
├── .pre-commit-config.yaml
├── Dockerfile
├── Dockerfile.dev
├── docker-compose.yml
├── Makefile
└── .env.sample
```

---

## ✅ Calidad de código y seguridad

Todas las herramientas están configuradas en `pyproject.toml` y se ejecutan automáticamente vía hooks de pre-commit en cada `git commit`.

### Hooks de pre-commit

| Hook                    | Propósito                                    |
|-------------------------|----------------------------------------------|
| `ruff`                  | Lint + auto-fix                              |
| `ruff-format`           | Formato                                      |
| `mypy`                  | Tipado estático (strict)                     |
| `bandit`                | Análisis estático de seguridad (SAST)        |
| `detect-secrets`        | Bloquea commits con secrets accidentales     |
| `detect-private-key`    | Bloquea archivos de clave privada            |
| `no-commit-to-branch`   | Bloquea commits directos a `main`/`master`   |

### Ejecutar los checks manualmente

```bash
make lint        # Ruff linter
make format      # Ruff formatter
make typecheck   # mypy
make audit       # pip-audit — escaneo de CVEs en dependencias
make sast        # bandit — análisis estático de seguridad
make check       # lint + typecheck juntos
```

### Mantener los hooks sincronizados

Las versiones en `requirements-dev.txt` (ruff, mypy, bandit) deben coincidir con los valores `rev:` en `.pre-commit-config.yaml`. Al actualizar una versión en `requirements-dev.txt`, ejecutar:

```bash
make sync-hooks
pre-commit clean && pre-commit install
```

`sync-hooks` lee las versiones desde `requirements-dev.txt` y actualiza `.pre-commit-config.yaml` automáticamente.

---

## 🧪 Testing

```bash
make test          # Ejecuta todos los tests
make test-cov      # Ejecuta con reporte de cobertura HTML
```

Los tests se encuentran en `tests/unit/` y `tests/integration/`.

---

## 🧱 Makefile

```
Setup
  make setup            Crea el venv, instala deps y registra los hooks de pre-commit
  make restart-setup    Elimina el venv y ejecuta setup desde cero
  make install-deps     Instala dependencias en el venv existente
  make sync-hooks       Sincroniza las revisiones de .pre-commit-config.yaml con requirements-dev.txt

Calidad de código
  make lint             Lint con ruff
  make format           Formato con ruff
  make typecheck        Tipado con mypy
  make audit            Escaneo de CVEs en dependencias (pip-audit)
  make sast             Análisis estático de seguridad (bandit)
  make check            lint + typecheck

Testing
  make test             Ejecuta la suite de tests
  make test-cov         Ejecuta tests con reporte de cobertura HTML

Ejecución
  make dev              Servidor de desarrollo con hot-reload
  make run              Servidor de producción

Docker
  make docker-up        Levanta la infraestructura (DB, Redis, LocalStack, NATS)
  make docker-down      Detiene todos los servicios
  make docker-logs      Sigue los logs de los servicios

Base de datos
  make migrate          Ejecuta las migraciones de Alembic
  make migrate-create   Crea una nueva migración (MSG="descripción")
  make migrate-rollback Revierte la última migración

SonarQube
  make sonar            Ejecuta el análisis contra el servidor SonarQube remoto

Utilidades
  make hooks-install    (Re)instala los hooks de pre-commit
  make clean            Elimina archivos de caché de Python
  make help             Muestra todos los targets con sus descripciones
```

---

## 🔍 SonarQube

El pipeline de CI ejecuta SonarQube automáticamente en cada pull request. También se puede ejecutar localmente contra el mismo servidor remoto para detectar problemas **antes de pushear**.

### Configuración (una sola vez)

1. Obtener la URL del servidor desde GitHub → Settings → Secrets and variables → Actions → pestaña **Variables** (`SONAR_HOST_URL`)
2. Generar un token personal: ingresar al servidor SonarQube → My Account → Security → Generate Token
3. Agregar ambos al `.env`:

```env
SONAR_HOST_URL=https://tu-servidor-sonarqube.com
SONAR_TOKEN=squ_xxxxxxxxxxxxxxxxxxxx
```

### Ejecutar el análisis

```bash
make sonar
```

Este comando:
1. Genera el reporte de cobertura (`coverage/coverage.xml`) ejecutando la suite de tests
2. Corre `sonar-scanner` vía Docker contra el servidor remoto
3. Imprime la URL del dashboard con los resultados

### Condiciones del quality gate

El proyecto debe pasar las 4 condiciones para poder mergear:

| Condición                  | Umbral       |
|----------------------------|--------------|
| Issues de severidad Blocker| 0            |
| Issues de seguridad        | ≤ 1          |
| Rating de confiabilidad    | C o mejor    |
| Rating de seguridad        | B o mejor    |

La cobertura se trackea pero no es una condición bloqueante actualmente.

---

## ➕ Agregar una nueva entidad de dominio

1. **Modelo de dominio** → `domain/models/tu_entidad.py`
2. **Puerto de repositorio** → extender `RepositoryPort[TuEntidad]` en `domain/ports/`
3. **Modelo ORM** → agregar en `adapters/db/sqlalchemy/models.py`
4. **Implementación del repositorio** → extender `SQLAlchemyRepository` en `adapters/db/sqlalchemy/`
5. **Unit of Work** → adjuntar el repo a una subclase de `SQLAlchemyUnitOfWork`
6. **Servicio de aplicación** → `services/application_services/tu_servicio.py`
7. **Router** → `api/routers/tu_router.py` y registrarlo en `main.py`
8. **Schemas** → `api/schemas.py`
9. **Migración** → `make migrate-create MSG="add tu_entidad table"`

---

## 🔄 Cambiar el backend de mensajería

Configurar `MESSAGING_BACKEND` en `.env`:

| Valor     | Adaptador usado                          |
|-----------|------------------------------------------|
| `sns_sqs` | `adapters/messaging/sns_sqs_adapter.py`  |
| `nats`    | `adapters/messaging/nats_adapter.py`     |

No se requieren cambios en el código de aplicación — la interfaz de puerto es idéntica.

---

## 💾 Cambiar el backend de storage

Configurar `STORAGE_BACKEND` en `.env`:

| Valor   | Adaptador usado                              |
|---------|----------------------------------------------|
| `local` | `adapters/storage/local_storage_adapter.py`  |
| `s3`    | Implementar `S3StorageAdapter` (ver TODO)     |

---

## 🔄 Flujo de commits (Conventional Commits)

Este proyecto aplica el estándar de Conventional Commits, compatible con Semantic Release y generación automática de changelogs.

### Formato obligatorio

```
<type>(<scope>): <resumen corto>

<body>

<footer>
```

### Tipos permitidos

| type       | uso                              |
|------------|----------------------------------|
| `build`    | cambios en build / dependencias  |
| `ci`       | pipelines / GitHub Actions       |
| `docs`     | documentación                    |
| `feat`     | nueva funcionalidad              |
| `fix`      | bugfix                           |
| `perf`     | mejoras de performance           |
| `refactor` | refactor sin feature ni bugfix   |
| `test`     | tests                            |

### Reglas clave

- Usar imperativo: `add`, `fix`, `change`
- Sin mayúscula inicial en el resumen
- Sin punto final
- `body` obligatorio excepto en `docs`
- `footer` para BREAKING CHANGE y referencias a issues

### Ejemplo válido

```
feat(api): add health check endpoint
```

### Ejemplo inválido

```
cambios varios
```
