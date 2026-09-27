# Run NilaSaatchi AI on Windows (no WSL, no bash)

## 1. Before you start (once)
1. Install **Docker Desktop**: https://www.docker.com/products/docker-desktop/ (accept the WSL 2 option it offers). Start it and wait until it says **Engine running**.
2. Have ~10 GB free disk and 8 GB RAM.

## 2. Put the files in place
1. Unzip `nilasaatchi-demo.zip` to a **short path**, e.g. `C:\nilasaatchi-demo` (long paths like Downloads\...\... can break npm).
2. Unzip `nilasaatchi-windows-addon.zip` **into that same folder**. When Windows asks, choose *Replace*. You should now see `SETUP-WINDOWS.cmd` and `START-WINDOWS.cmd` next to `DEMO.md`.

## 3. One-time setup (15–25 min)
Double-click **`SETUP-WINDOWS.cmd`**. It installs uv, Python 3.12, Node.js (via winget), the packages and the database, then restores all the data. It is safe to run again if something stopped it.
- **"Windows protected your PC":** click *More info* → *Run anyway* (the files are plain scripts).
- **"Node.js installed; close this window and run again":** do exactly that; Windows needs a new window to see Node.

## 4. Start the app (every time)
Double-click **`START-WINDOWS.cmd`**. The browser opens **http://localhost:5173**. Keep the black window open, and press **Enter** in it to stop.

## Optional
- **Agent console:** open `.env` in Notepad and paste the free keys `GEMINI_API_KEY`, `GROQ_API_KEY`, `COHERE_API_KEY`.
- **Uploading new documents** needs the team AWS login (ask the team lead). Every other page works without it.
- **Cloud version (nothing to install):** https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/

## If something goes wrong
| Message | Fix |
|---|---|
| "Start Docker Desktop first" | Open Docker Desktop, wait for *Engine running*, run again |
| "Docker Desktop is installed but not running" | Same as above |
| Setup stops at `uv sync` | Run SETUP-WINDOWS.cmd again (it retries without the optional OCR package) |
| "API did not start" | Open `data\logs\api.err.log` and send the last lines to the team lead |
| Page says "Cannot reach the server" | The black START window was closed; double-click START-WINDOWS.cmd again |
| Port 5439/8000/5173 in use | Another copy is running; close it, or restart the PC |
