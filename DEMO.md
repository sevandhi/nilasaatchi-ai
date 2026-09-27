# Run NilaSaatchi AI on a new machine

## What you need
- **OS:** Ubuntu/Debian Linux, macOS, or Windows 10/11 with **WSL2** (run everything inside the Ubuntu terminal).
- **Docker:** Docker Desktop (macOS/Windows) or Docker Engine (Linux), **running**.
- **Disk and memory:** ~10 GB free disk, 8 GB RAM.
- **Internet:** needed for the one-time install, satellite chips and the AI models.
- **Everything else is installed by the setup script:** uv, Python 3.12, Node 20, the Python/JS packages, the database and OCR images, and headless Chromium.

## 1. Get the bundle
Copy `nilasaatchi-demo.zip` (about 2.3 GB) to the new machine and unzip it (Windows: copy it into the WSL Ubuntu home folder first), then:
```bash
unzip nilasaatchi-demo.zip        # Ubuntu: sudo apt install -y unzip
cd nilasaatchi-demo
```
The bundle holds real land records (owner names are masked in the app). Share it only within the team.

## 2. One-time setup (10–20 min)
```bash
bash scripts/demo_setup.sh      # or: make demo-setup
```
It checks Docker, installs the tools, builds the images, restores the database (2,362 documents, 1,242 parcels, 1,388 findings) and unpacks the data files. It is safe to re-run.

**AI keys (optional but recommended):** open `.env` and paste the free keys for `GEMINI_API_KEY`, `GROQ_API_KEY` and `COHERE_API_KEY`. They are needed for the Agent console. Without keys, every other page works from the stored data.

**Uploading new documents** needs the AWS table reader: `make aws-login` (a device code approved in the browser; valid ~2 h). The setup script adds the team's AWS profiles if the AWS CLI is installed.

## 3. Start everything
```bash
bash scripts/demo_run.sh        # or: make demo
```
It starts the database → API (http://localhost:8000) → web UI (**http://localhost:5173**) and opens the browser. Press **Ctrl-C** to stop the API and UI (`docker compose stop db` stops the database).

## 4. Demo material
- Page-by-page guide, test inputs and expected results: `docs/ui/01-user-guide.md`, `02-testing-guide.md`, `03-demo-test-data.md`.
- Upload demo files: `data/demo_uploads/` (edge cases) and `data/demo_uploads/working/` (20 documents that link parcels; see its README). After a demo: `uv run python scripts/demo_uploads.py cleanup`.
- The downloadable report explained: `docs/ui/04-report-guide.md`, with samples in `data/demo_uploads/working/reports/`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Docker is installed but not running" | Start Docker Desktop, or on Linux: `sudo usermod -aG docker $USER`, then log out and in |
| Port 5439 / 8000 / 5173 in use | Stop the other program, or it is an earlier NilaSaatchi run still going (`demo_run.sh` reuses it) |
| UI says "Cannot reach the server" | The API is not up: check `data/logs/api.log` |
| "Reading tables" step fails on upload | AWS not signed in: `make aws-login` |
| Agent console answers "no eligible model" | Free AI quota for the day is used up, or `.env` has no keys |
| PDF report download fails (501) | `uv run playwright install --with-deps chromium` |
