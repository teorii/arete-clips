@echo off
rem Start the self-hosted object store. Leave this window open: the app cannot
rem serve or accept clips while it is closed.
setlocal enabledelayedexpansion
cd /d "%~dp0.."

rem Credentials come from .env so they live in exactly one place.
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
  if "%%a"=="S3_ACCESS_KEY_ID" set "MINIO_ROOT_USER=%%b"
  if "%%a"=="S3_SECRET_ACCESS_KEY" set "MINIO_ROOT_PASSWORD=%%b"
)

if "%MINIO_ROOT_USER%"=="" (
  echo S3_ACCESS_KEY_ID is not set in .env
  pause
  exit /b 1
)

set "MINIO=%LOCALAPPDATA%\Microsoft\WinGet\Packages\MinIO.Server_Microsoft.Winget.Source_8wekyb3d8bbwe\minio.exe"
if not exist "%MINIO%" (
  echo MinIO not found. Install it with:  winget install --id MinIO.Server -e
  pause
  exit /b 1
)

echo Object store on http://127.0.0.1:9000  (console on :9001)
"%MINIO%" server ".minio-data" --address "127.0.0.1:9000" --console-address "127.0.0.1:9001"
