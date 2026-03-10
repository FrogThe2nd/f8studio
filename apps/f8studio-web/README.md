# f8studio-web (WIP)

Minimal React + `@xyflow/react` frontend for the headless backend `python -m f8pystudio.web`.

## Dev
1. Start backend:
   - `H:\Feel8\f8studio\.pixi\envs\default\python.exe -m f8pystudio.web --http-port 8765 --ws-port 8766`
2. Install frontend deps (once) and run dev server:
   - `npm install`
   - `npm run dev`

Backend endpoints used:
- `GET/PUT /api/v1/session/last`
- `POST /api/v1/graph/normalize`
- `POST /api/v1/graph/validate-connection`
- `POST /api/v1/session/export/nodegraphqt`

