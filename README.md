# SQLite + Flask + Vue Project

This project is a minimal full-stack starter using SQLite, Flask, and Vue.

## Backend

- Python + Flask
- SQLite database with SQLAlchemy
- CORS enabled for local development

## Frontend

- Vue 3 + Vite
- Proxy configured for `/api` endpoints

## Setup

### 1. Backend

1. Create and activate a Python virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

2. Install backend dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the backend server:

```powershell
python backend/app.py
```

The backend will run on `http://127.0.0.1:5000`.

### 2. Frontend

1. Install frontend dependencies:

```powershell
cd frontend
npm install
```

2. Start the frontend dev server:

```powershell
npm run dev
```

The frontend will run on `http://localhost:5173` and proxy API requests to the backend.

## Project structure

- `backend/app.py` — Flask API and SQLite setup
- `frontend/` — Vue app with Vite
- `.gitignore` — ignores virtual environment, build output, and database files
