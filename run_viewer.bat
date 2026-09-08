@echo off
title Project Zero 3D Viewer & Extractor (GPU Desktop)
cd /d "%~dp0"
python pz_viewer.py
if errorlevel 1 pause
