# Videoflix Backend

REST API backend for Videoflix — a video streaming platform. Built with Django, served via Gunicorn, and containerized with Docker Compose.

---

## Tech Stack

| Component      | Technology                          |
|---------------|--------------------------------------|
| Framework     | Django 6.0.4 + Django REST Framework |
| Auth          | JWT via `djangorestframework-simplejwt` (HttpOnly cookies) |
| Database      | PostgreSQL (via `psycopg2-binary`)   |
| Cache / Queue | Redis + django-rq (RQ worker)        |
| Video         | FFmpeg (HLS transcoding)             |
| Static files  | WhiteNoise                           |
| Container     | Docker + Docker Compose              |

---

## Project Structure

```
├── auth_app/           # Registration, login, logout, password reset, JWT
├── content_app/        # Video upload, FFmpeg transcoding, HLS streaming
├── core/               # Django settings, root URLs, WSGI/ASGI
├── media/              # Uploaded and transcoded video files (volume-mounted)
├── static/             # Collected static files (volume-mounted)
├── backend.Dockerfile  # Docker image definition (Python 3.12 Alpine)
├── backend.entrypoint.sh # Container startup script
├── docker-compose.yml  # Service orchestration
├── requirements.txt    # Python dependencies
└── manage.py
```

---

## Services (Docker Compose)

| Container            | Role                                              |
|----------------------|---------------------------------------------------|
| `videoflix_backend`  | Gunicorn web server on port 8000                  |
| `videoflix_worker`   | RQ worker for background jobs (email, transcoding)|
| `videoflix_database` | PostgreSQL database                               |
| `videoflix_redis`    | Redis (cache + job queue)                         |

---

## Setup

### 0. Requirements

Make sure [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) are installed.

| OS            | Install                                                                 |
|---------------|-------------------------------------------------------------------------|
| Windows / Mac | [Docker Desktop](https://www.docker.com/products/docker-desktop/)       |
| Linux         | `curl -fsSL https://get.docker.com | sudo sh` + Docker Compose plugin  |

### 1. Clone and configure environment

Copy the example and fill in your values:

```bash
# Linux / macOS
cp .env.example .env

# Windows (PowerShell)
copy .env.example .env
```

Edit `.env`:

```env
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_PASSWORD=yourpassword
DJANGO_SUPERUSER_EMAIL=admin@example.com

SECRET_KEY='your-secret-key'
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:5500,http://127.0.0.1:5500

DB_NAME=videoflix
DB_USER=videoflix_user
DB_PASSWORD=yourdbpassword
DB_HOST=db
DB_PORT=5432

REDIS_HOST=redis
REDIS_LOCATION=redis://redis:6379/1
REDIS_PORT=6379
REDIS_DB=0

EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your@email.com
EMAIL_HOST_PASSWORD=yourpassword
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=your@email.com

# Set to True in production (requires HTTPS)
JWT_COOKIE_SECURE=False
```

> **Note:** If your `EMAIL_HOST_PASSWORD` contains `$` characters, escape them as `$$` in `.env` when used with Docker Compose.

> **Note:** After cloning the repository, make sure the files `auth_app/api/__init__.py` and `content_app/api/__init__.py` exist. Git does not track empty files by default, which can cause a `ModuleNotFoundError: No module named 'auth_app.api'` on a fresh clone. If they are missing, create them as empty files.

### 2. Build and start all services

```bash
# First start or after Dockerfile/requirements changes
docker compose up -d --build

# Normal start (if images are already built)
docker compose up -d
```

On first start, the `web` container automatically runs:
- `collectstatic`
- `makemigrations` + `migrate`
- Superuser creation (from env variables)

### 3. Access the API

- API: `http://localhost:8000/api/`
- Admin: `http://localhost:8000/admin/`

---

## API Endpoints

### Auth (`/api/auth/`)

| Method | Endpoint                                    | Description                  |
|--------|---------------------------------------------|------------------------------|
| POST   | `register/`                                 | Register new user (inactive) |
| GET    | `activate/<uidb64>/<token>/`               | Activate account via email   |
| POST   | `login/`                                    | Login, sets JWT cookies      |
| POST   | `logout/`                                   | Logout, clears JWT cookies   |
| POST   | `token/refresh/`                            | Refresh access token         |
| POST   | `password_reset/`                           | Request password reset email |
| POST   | `password_confirm/<uidb64>/<token>/`       | Set new password             |

### Content (`/api/content/`)

| Method | Endpoint                                          | Description                      |
|--------|---------------------------------------------------|----------------------------------|
| GET    | `video/`                                          | List all available videos        |
| GET    | `video/<id>/<resolution>/index.m3u8`             | HLS playlist (480p/720p/1080p)   |
| GET    | `video/<id>/<resolution>/<segment>`              | HLS video segment                |

All content endpoints require authentication (JWT via HttpOnly cookie).

---

## Video Processing

Videos are uploaded via the Django admin. The `content_app` automatically transcodes uploads to three HLS resolutions using FFmpeg:

- `480p`
- `720p`
- `1080p`

Transcoding runs as a background job via the RQ worker.

---

## Running Tests

Tests werden mit **pytest** ausgeführt. Coverage wird automatisch mitgemessen und am Ende angezeigt — kein separater Befehl nötig.

### Lokal (ohne Docker)

Voraussetzung: virtuelle Umgebung aktivieren und Dependencies installiert haben (siehe unten).

```bash
# Linux / macOS
.venv/bin/python -m pytest

# Windows (PowerShell)
.venv\Scripts\python.exe -m pytest
```

Die Tests laufen lokal gegen **SQLite** — kein laufendes Docker/PostgreSQL/Redis erforderlich.

### Im Docker-Container

Zuerst alle Dienste starten (falls noch nicht laufend):

```bash
docker compose up -d
```

Dann Tests ausführen:

```bash
docker compose exec web python -m pytest
```

Im Container laufen die Tests gegen **PostgreSQL** (wie in Produktion).

### Virtuelle Umgebung einrichten (einmalig)

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Konfiguration

Pytest ist über `setup.cfg` konfiguriert (`[tool:pytest]`):
- `DJANGO_SETTINGS_MODULE = core.settings`
- Coverage wird automatisch via `pytest-cov` gemessen (Quelle: `auth_app`, `content_app`, `core`)
- Fehlende Zeilen werden im Report angezeigt (`--cov-report=term-missing`)

### Aktuelle Abdeckung

```
Name                             Stmts   Miss  Cover
----------------------------------------------------
auth_app\admin.py                   39      2    95%
content_app\api\serializers.py      13      1    92%
content_app\api\views.py            48      3    94%
core\settings.py                    46      2    96%
----------------------------------------------------
TOTAL                              690     16    98%
```

97 Tests — **98% Coverage**

---

## Code Quality

```bash
# Linux / macOS
.venv/bin/python -m pycodestyle .
.venv/bin/python -m pydocstyle .

# Windows (PowerShell)
.venv\Scripts\python.exe -m pycodestyle .
.venv\Scripts\python.exe -m pydocstyle .
```

Both checks pass with 0 violations.

---

## Useful Commands

```bash
# View worker logs
docker compose logs -f worker

# View web logs
docker compose logs -f web

# Full reset (deletes all volumes)
docker compose down -v --remove-orphans
docker compose up -d

# Open Django shell in container
docker compose exec web python manage.py shell
```
