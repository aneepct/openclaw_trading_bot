@echo off
echo =============================
echo   OPEN CLAW (scanner + UI)
echo =============================

:: Start backend
echo [1/2] Starting FastAPI backend on port 8001...
start "Open Claw Backend" cmd /k "cd backend && pip install -r requirements.txt && python -m uvicorn main:app --reload --port 8001"

:: Wait a moment then start frontend
timeout /t 3 /nobreak >nul
echo [2/2] Starting React frontend on port 3001...
start "Open Claw Frontend" cmd /k "cd frontend && npm install && npm start"

echo.
echo Backend:  http://localhost:8001
echo Frontend: http://localhost:3001
echo API Docs: http://localhost:8001/docs
echo.
pause
