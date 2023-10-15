# Running instructions

1. Install Docker
2. Install Python 3.11 and Poetry
3. Install Node.js v18
4. In the `frontend` directory: `npm i && npm run build`
5. In the root directory: `poetry shell`, then `poetry install`
6. Start the backend: `uvicorn game.ws_app:create_app --factory --host 127.0.0.1 --port 8089`
7. Start the reverse proxy: `docker compose up`
7. Open `http://127.0.0.1:12345` in the browser