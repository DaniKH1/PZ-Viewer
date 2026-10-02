@echo off
title "Project Zero / Fatal Frame 3D Viewer & Extractor"
cd /d "%~dp0"
python pz_viewer.py
if errorlevel 1 pause
