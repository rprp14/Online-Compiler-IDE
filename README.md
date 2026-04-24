# Cloud IDE Upgrade

This project upgrades the existing online compiler into a cloud-style browser IDE without throwing away the original codebase. It keeps the lightweight Flask + static frontend structure, but adds a much richer workspace model and a VS Code-inspired interface.

## What Changed

- User authentication with private per-user workspaces
- Hierarchical file explorer with nested folders
- File create, rename, move, delete, and drag/drop organization
- Version history for every saved file
- Autosave support for open files
- File search
- File sharing between users with `read` and `write` permissions
- Cloud storage abstraction:
  - Local object storage by default
  - Optional AWS S3 storage via environment variables
- Multi-language execution:
  - Python
  - C
  - C++
  - Java
  - JavaScript
  - HTML preview
- Docker-ready execution path with local fallback
- VS Code-like dark UI with explorer, editor, terminal, preview, and activity panels
- Dockerfile, `docker-compose.yml`, and GitHub Actions CI

## Tech Stack

- Backend: Flask REST-style API
- Frontend: existing static frontend upgraded with CodeMirror
- Database: SQLite by default
- Cloud storage: local object storage or AWS S3
- Execution: local process fallback or Docker containers

## Folder Structure

```text
online-compiler/
├── backend/
│   ├── app.py
│   ├── manage.py
│   └── data/
├── frontend/
│   ├── index.html
│   ├── script.js
│   └── style.css
├── .github/
│   └── workflows/
│       └── ci.yml
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Main APIs

- `POST /api/signup`
- `POST /api/login`
- `POST /api/logout`
- `GET /api/session`
- `GET /api/dashboard`
- `GET /api/workspace/tree`
- `POST /api/bootstrap`
- `POST /api/folders`
- `PUT /api/folders/<id>`
- `DELETE /api/folders/<id>`
- `POST /api/files`
- `GET /api/files/<id>`
- `PUT /api/files/<id>`
- `POST /api/files/<id>/autosave`
- `DELETE /api/files/<id>`
- `GET /api/files/search?q=...`
- `GET /api/files/<id>/versions`
- `GET /api/versions/<id>`
- `GET /api/shares`
- `POST /api/shares`
- `DELETE /api/shares/<id>`
- `GET /api/history`
- `GET /api/monitor`
- `POST /run`

## Run Locally

1. Open a terminal in `online-compiler`.
2. Create a virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Start the backend:

```bash
python backend/app.py
```

5. Open `http://127.0.0.1:5000`.

## Maintenance Commands

To inspect duplicate folders/files before removing anything:

```bash
python backend/manage.py cleanup-duplicates
```

To permanently remove the old duplicate rows:

```bash
python backend/manage.py cleanup-duplicates --apply
```

## Optional Cloud Storage Setup

Default mode stores file snapshots in `backend/data/object_storage`.

To use AWS S3, set:

```env
STORAGE_BACKEND=s3
AWS_S3_BUCKET=your-bucket-name
AWS_REGION=ap-south-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

## Optional Docker Runtime

To enable Docker-based execution, install Docker and set:

```env
ENABLE_DOCKER_EXECUTION=true
```

When Docker is disabled or unavailable, the app falls back to local execution tools already installed on the machine.

## Integration Guide

1. Install Python dependencies from `requirements.txt`.
2. Run the app once so SQLite creates or upgrades the database tables.
3. Register a user account.
4. The app bootstraps a starter file in your private workspace.
5. Create nested folders and files from the Explorer.
6. Open a file to edit it in CodeMirror.
7. Changes autosave after a short pause.
8. Use drag and drop in the Explorer to move files or folders.
9. Share the selected file with another username using `read` or `write`.
10. Run supported languages from the terminal area.
11. Enable S3 and Docker later by adding environment variables.

## Docker Deployment

Build and run:

```bash
docker compose up --build
```

The app will be available on port `5000`.

## CI/CD

GitHub Actions workflow added at `.github/workflows/ci.yml`.

Current pipeline:

- installs Python
- installs dependencies
- runs Python bytecode compilation for backend files

You can extend it later with deployment jobs for Render, AWS, or your preferred platform.

## Deployment Suggestions

### Render

- Create a new Web Service
- Use `pip install -r requirements.txt` as build command
- Use `python backend/app.py` as start command
- Add environment variables for secret key, S3, and Docker settings if needed

### AWS EC2 / ECS

- Use the included Dockerfile
- Mount a persistent volume if you want local object storage to survive restarts
- Prefer S3 for durable file snapshot storage

### Vercel

- Vercel is best for frontend-only projects
- This app has a Python backend runtime, so Render or AWS is a better fit

## Notes

- The backend stays Flask-based to extend the current project safely instead of replacing it from scratch.
- SQLite is preserved as the existing database, which satisfies the “existing DB” option.
- Docker execution is safer than local execution, but it still needs production hardening for a public internet deployment.
