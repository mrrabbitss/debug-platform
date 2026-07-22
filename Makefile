.PHONY: backend frontend test demo docker
backend:
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
frontend:
	cd frontend && npm run dev
test:
	cd backend && pytest -q
	cd frontend && npm run build
	cd vscode-extension && npm run compile
demo:
	python scripts/seed_demo.py
docker:
	docker compose up --build
