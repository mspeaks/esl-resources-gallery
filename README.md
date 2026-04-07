# ESL Resources Gallery

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)

A self-hosted gallery for ESL teaching teams to share, organize, and review photocopiable classroom resources. Upload a PDF, and the system converts it to browsable pages and uses AI to auto-generate metadata — title, grammar tags, CEFR level, activity type.

Built for language schools, in-company training programs, and ESL departments that want a simple, private, self-managed resource library without relying on third-party platforms.

**[Live Demo](https://apps.schenker.blog/esl/)**

<!-- 
## Screenshots

To add screenshots, place them in a `docs/` folder and reference them here:

![Gallery View](docs/screenshots/gallery.png)
![Lightbox Viewer](docs/screenshots/lightbox.png)
![Upload Flow](docs/screenshots/upload.png)
-->

## Features

- **Browse & filter** by CEFR level (A1-C2), grammar topic, activity type, and free text search
- **PDF upload** with automatic page conversion and AI metadata extraction (via Google Gemini)
- **Lightbox viewer** with keyboard/touch navigation and PDF download
- **Two roles**: teachers (upload, log, annotate) and coordinators (approve, manage users)
- **Class log** with star ratings per resource
- **Teacher notes** per resource (editable by uploader or coordinator)
- **Approval queue** for coordinator review of new uploads
- **Mobile-first** responsive design with print CSS
- **Zero frontend dependencies** — single HTML file, no build step

## Architecture

```
frontend/          Static single-file app (HTML + CSS + vanilla JS)
api/               FastAPI backend (Python) + SQLite database
```

The frontend talks to the API at `/esl-api/` (configurable). The API handles auth, resource CRUD, PDF processing, and AI analysis.

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/mspeaks/esl-resources-gallery.git
cd esl-resources-gallery
cp .env.example .env
# Edit .env — at minimum set ESL_ADMIN_PASS and GOOGLE_API_KEY
```

### 2. Start the API

```bash
cd api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Load env vars
export $(grep -v '^#' ../.env | xargs)
python3 -m uvicorn main:app --host 127.0.0.1 --port 8915
```

### 3. Serve the frontend

```bash
# Any static file server works
cd frontend
python3 -m http.server 8080
```

Then visit `http://localhost:8080`.

### 4. Configure your reverse proxy

For production, put both behind nginx (or similar). Example config:

```nginx
location /esl/ {
    alias /path/to/esl-resources-gallery/frontend/;
}
location /esl-api/ {
    proxy_pass http://127.0.0.1:8915/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | For uploads | — | Gemini API key for AI metadata extraction |
| `ESL_ADMIN_USER` | No | `admin` | Username for the seeded coordinator account |
| `ESL_ADMIN_PASS` | On first run | — | Password for the seeded coordinator (empty = skip seeding) |
| `ESL_CORS_ORIGINS` | Yes | `http://localhost:8080` | Comma-separated allowed origins |
| `ESL_DB_PATH` | No | `./gallery.db` | SQLite database path |
| `ESL_PDF_DIR` | No | `./media/pdf` | Directory for uploaded PDFs |
| `ESL_IMG_DIR` | No | `./media/img` | Directory for converted page images |
| `ESL_SESSION_DAYS` | No | `30` | Session cookie lifetime in days |

## System Dependencies

- Python 3.10+
- `pdftoppm` (from `poppler-utils`) — for PDF-to-image conversion

```bash
# Debian/Ubuntu
sudo apt install poppler-utils
```

## Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request.

## License

[MIT](LICENSE)
