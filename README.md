# Mihango School Management System

A multi-school Django application for school administration, teaching and learning, student/guardian access, academics, finance, staff workflows, reporting, and notifications. Each school is resolved from a hostname subdomain and receives its own school-branded login page. The application is deployed as a Django web service (configured for Render), uses PostgreSQL for production data, Redis-compatible Key Value for shared cache/rate limiting, and private S3-compatible Supabase Storage for uploaded files.

> This README describes the code currently in this repository. It is not a security certification or a substitute for production operational controls.

## Contents

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Repository layout](#repository-layout)
- [Local development](#local-development)
- [Tenant hosts](#tenant-hosts)
- [Tests and checks](#tests-and-checks)
- [Production deployment](#production-deployment)
- [Environment configuration](#environment-configuration)
- [Security and data handling](#security-and-data-handling)
- [Operational notes](#operational-notes)

## Capabilities

- Multi-tenant school configuration, school-specific domains and branded login pages.
- User and role management, forced first-login password change, account lockout, and idle-session controls.
- School configuration for academic years and terms, classes/streams, subjects, assessment structures, grading schemes, and timetables.
- Student enrollment and import, guardian/family relationships, teacher/staff records, attendance, leave, and staff attendance.
- Assessment mark entry, approval workflows, report cards, transcripts, and historical academic records.
- Fee structures with term-based tuition items, optional charges and transport; invoices, payments, allocations, receipts, arrears, and refunds.
- Parent/student, teacher, principal/academic, manager, finance, staff, deputy principal, and super-admin dashboards, subject to role and school scope.
- Learning materials, assignments, quizzes, notifications, announcements, and attendance SMS provider integrations.
- REST API endpoints for profile, courses, students, attendance, and notifications, with OpenAPI schema/documentation routes.
- CSV student import through a management command and spreadsheet/document import workflows in the dashboard.

## Architecture

The project is a single Django application organized around one `smsApp` domain app. Django templates serve the browser dashboards; Django REST Framework exposes a versioned API for client integrations. Both interfaces use the same models and service/query logic where implemented.

Tenant resolution is host-based. `TenantMiddleware` reads `TENANT_ROOT_DOMAIN`, extracts the single leftmost hostname label, and looks up an active `School` whose `subdomain` exactly matches it. Unknown/inactive tenant hosts return 404. The school boundary is enforced through role-aware view mixins and school-scoped querysets; the hostname is not a substitute for per-user authorization.

Authentication for browser dashboards uses Django sessions. API authentication uses SimpleJWT. `AccountSecurityMiddleware` handles locked accounts, mandatory password rotation, and browser inactivity tracking. Django cache is backed by Redis/Render Key Value when `REDIS_URL` is configured; development can fall back to per-process local memory.

### Main domain areas

`smsApp/models.py` contains the custom user, school, academic structure, students/guardians/staff, attendance, assessment/reporting, finance, library, timetable, LMS, notifications, leave, and audit models. `smsApp/services.py` contains shared business operations. `smsApp/views.py` contains the server-rendered dashboard flows. API serializers, permissions, viewsets, and URL routing are under `smsApp/api/`.

## Technology stack

- Python 3.12 (the Render Blueprint pins this version) and Django 6.1.
- PostgreSQL with Psycopg 3 for production; SQLite is the local fallback when `DATABASE_URL` is omitted.
- Django REST Framework, SimpleJWT, and drf-spectacular for the API.
- Redis-compatible Django cache (`django-redis`) for cross-worker cache/rate-limit state.
- Supabase S3-compatible private object storage via `django-storages` and `boto3`.
- WhiteNoise for static assets; Gunicorn as the WSGI server.
- WeasyPrint/Pango/Cairo for PDF rendering; `pypdf` for PDF processing; `openpyxl` for spreadsheet handling; `python-magic` for upload content validation.
- Render Blueprint for deployment; Supabase PostgreSQL and Storage are configured externally.

Python dependency pins are maintained in [`SMS/requirements.txt`](SMS/requirements.txt). System packages needed at build time are installed by [`SMS/deploy/build.sh`](SMS/deploy/build.sh).

## Repository layout

```text
.
├── README.md
├── .gitignore
└── SMS/
    ├── manage.py                 # Django management entry point
    ├── requirements.txt          # Python dependencies
    ├── .env.example              # Safe variable-name/configuration template
    ├── Procfile                  # Gunicorn process declaration
    ├── render.yaml               # Render web + Key Value Blueprint
    ├── deploy/build.sh           # Install system/Python packages, collectstatic, migrate
    ├── SMS/
    │   ├── settings/{base,dev,prod}.py
    │   ├── urls.py                # Admin, dashboard and API roots
    │   ├── wsgi.py / asgi.py
    ├── smsApp/
    │   ├── models.py              # Domain schema and custom User
    │   ├── views.py               # Dashboard and workflow endpoints
    │   ├── services.py            # Shared application/business logic
    │   ├── permissions.py         # Browser role access helpers
    │   ├── middleware.py          # Tenant + account/session security
    │   ├── validators.py           # File upload validation
    │   ├── sms.py                 # SMS provider adapters
    │   ├── api/                   # DRF endpoints, serializers, permissions
    │   ├── management/commands/   # Import and maintenance commands
    │   ├── migrations/            # Database schema history
    │   └── test*.py               # Django test modules
    ├── templates/                 # Shared, dashboard and report templates
    ├── static/                    # Application-owned static source files
    ├── staticfiles/               # Collected/static distribution assets
    └── media/                     # Local development uploads only; do not commit
```

## Local development

Run commands from the `SMS/` directory. Python 3.12 is recommended to match deployment.

```bash
cd SMS
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env                 # Windows PowerShell: Copy-Item .env.example .env
```

Set at least a unique `SECRET_KEY` in `.env`. For an isolated local setup, leave `DATABASE_URL` empty to use `SMS/db.sqlite3`; do not point tests or development commands at the production database. Then:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 8005
```

The entry point defaults to `SMS.settings.dev`. Development settings enable debug, allow localhost hosts, use console email, and store uploaded files locally unless Supabase storage is explicitly enabled. Create/configure the school and its subdomain through the super-admin school configuration.

### Optional local services

- Redis: set `REDIS_URL` to a local or development Redis instance to test shared cache behavior. Without it, local-memory cache is used.
- Supabase Storage: set `USE_SUPABASE_STORAGE_IN_DEV=True` and the `SUPABASE_STORAGE_*` variables only when intentionally testing remote file storage.
- SMS: choose `SMS_PROVIDER` and supply that provider’s variables. Use provider sandbox/test numbers where available; do not send production messages from development by accident.

## Tenant hosts

For local multi-school testing, keep:

```dotenv
TENANT_ROOT_DOMAIN=localhost
ALLOWED_HOSTS=localhost,127.0.0.1,.localhost
```

If a school’s `subdomain` field is `mihango-high`, open `http://mihango-high.localhost:8005/login/`. If it is `mihango-ttc`, use `http://mihango-ttc.localhost:8005/login/`. The dashboard short name is only a display label; it does not select the tenant. Each subdomain must be unique and the school must be active.

For production, use a domain controlled by the school operator, configure a wildcard custom domain and DNS to the Render service, and set `TENANT_ROOT_DOMAIN` to the exact root domain (no scheme and no `*.`). For example, with `schools.example.org`, a school whose subdomain is `mihango-high` is reached at `https://mihango-high.schools.example.org`.

## Tests and checks

```bash
cd SMS
python manage.py check
python manage.py test
```

The test runner creates a test database. Confirm the development environment is using a disposable local database before running tests; never run tests against production data. To force a temporary SQLite database for an audit/test run:

```bash
REDIS_URL= DATABASE_URL=sqlite:////tmp/sms-test.sqlite3 python manage.py test
```

Useful specialized checks include tenant portal tests (`smsApp.test_tenant_portals`), API tests (`smsApp.api.tests`), finance workflow tests (`smsApp.test_finance_workflows`), and branding tests (`smsApp.test_branding`).

## Production deployment

The repository contains [`SMS/render.yaml`](SMS/render.yaml), a Render Blueprint defining a Python web service and Redis-compatible Key Value service. It expects an external Supabase PostgreSQL database and Supabase Storage credentials to be configured as environment variables. The Blueprint’s Redis service reference injects its internal connection URL as `REDIS_URL` when provisioned as a Blueprint. For a manually created service, configure `REDIS_URL` in the web service using the Key Value instance’s internal URL; keep the services in the same Render region.

The build script installs WeasyPrint/libmagic operating-system dependencies, installs Python packages, collects static files, and applies migrations. The web process is Gunicorn (`SMS.wsgi:application`) bound to Render’s `$PORT`.

Typical Render environment configuration includes:

- `DJANGO_SETTINGS_MODULE=SMS.settings.prod`
- `SECRET_KEY` (unique, random, private)
- `DATABASE_URL` (Supabase PostgreSQL connection string)
- `TENANT_ROOT_DOMAIN`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`
- `REDIS_URL` (Render Key Value internal URL)
- `SUPABASE_STORAGE_ENDPOINT_URL`, `SUPABASE_STORAGE_ACCESS_KEY_ID`, `SUPABASE_STORAGE_SECRET_ACCESS_KEY`, and bucket/region settings
- SMTP values if production email is enabled
- SMS provider credentials/settings if attendance SMS is enabled

Production settings fail closed if the controlled tenant root domain or Supabase Storage credentials are missing. They set `DEBUG=False`, HTTPS redirects, secure/HTTP-only session cookies, CSRF cookies, HSTS, content-type protection, and clickjacking protection. Review every environment variable in the Render dashboard after deployment; never place credentials in `render.yaml`.

## Environment configuration

[`SMS/.env.example`](SMS/.env.example) documents supported configuration. Do not put real values in that example file. Main variables include:

| Area | Variables |
|---|---|
| Django and host routing | `SECRET_KEY`, `DJANGO_SETTINGS_MODULE`, `TENANT_ROOT_DOMAIN`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `TIME_ZONE` |
| Database and cache | `DATABASE_URL`, `DB_CONN_MAX_AGE`, `REDIS_URL` |
| Object storage | `SUPABASE_STORAGE_ENDPOINT_URL`, `SUPABASE_STORAGE_ACCESS_KEY_ID`, `SUPABASE_STORAGE_SECRET_ACCESS_KEY`, `SUPABASE_STORAGE_BUCKET_NAME`, `SUPABASE_STORAGE_REGION`, `SUPABASE_STORAGE_URL_EXPIRE_SECONDS` |
| Email | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` |
| Attendance SMS | `SMS_PROVIDER`, `SMS_ATTENDANCE_NOTIFICATIONS`, `SMS_SENDER_ID`, and provider-specific credentials (`SMS_API_*`, `AFRICASTALKING_*`, or `TWILIO_*`) |

Environment variables hold **secrets and deployment configuration**, not school/student records. Persistent business records belong in PostgreSQL; uploaded files belong in private object storage. `.env` is ignored by Git; `.env.example` should contain names and safe placeholders only.

## Security and data handling

- Never commit `.env`, database snapshots, production exports, uploaded files, reports, transcripts, receipts, or student/staff documents.
- Keep Supabase Storage private and serve sensitive objects only through authorized views and expiring signed URLs.
- Use separate production credentials, rotate credentials if they were exposed, and restrict access to Render/Supabase dashboards.
- Keep `DEBUG=False` in production; use HTTPS, secure cookies, CSRF protections, tenant-scoped querysets, and server-side permission checks.
- Give users least-privilege roles and verify school scope for every read/write workflow. Dashboard visibility is not authorization.
- Use strong unique passwords, require temporary-password rotation, and do not distribute credentials through logs or plain email.
- Limit upload size and validate file content, not only filename extensions. Review uploaded content, storage access, and malware-scanning needs for your risk profile.
- Back up PostgreSQL and object storage, test restoration, define retention, and protect exports. Render free services are not a production reliability/backup strategy.
- Audit logs and login history contain sensitive operational metadata; restrict their access and define retention.

## Operational notes and known audit follow-ups

The local working tree originally contained a tracked SQLite database and tracked files beneath `SMS/media/`, including generated student reports/transcripts and uploaded documents. These paths have now been staged for removal from Git tracking, and `.gitignore` excludes them going forward; local working copies were preserved. **This does not remove copies from prior Git history or from any remote already pushed.** If the repository was pushed, assess whether history must be purged and rotate any credentials or private data that were exposed. Private student files should remain in the configured private object store, not in Git.

Review the `import_students` management command before operational use: it currently prints generated temporary student credentials to command output. Treat that output as sensitive and avoid storing it in CI/build/deployment logs; a secure one-time credential delivery workflow is preferable.

For high-assurance production, add automated dependency vulnerability scanning, secret scanning, backup/restore drills, centralized security logging/alerting, and an independent penetration test. The checks performed for this README are limited to repository/configuration inspection and Django checks/tests; they do not establish that the full deployed service is vulnerability-free.
