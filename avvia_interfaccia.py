#!/usr/bin/env python3
"""Avvia l'interfaccia grafica e apre il browser quando è pronta. Per chiuderla: chiudi questa finestra."""
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8501
URL = f"http://localhost:{PORT}"
HERE = Path(__file__).resolve().parent

proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(HERE / "interfaccia.py"),
                         "--server.headless", "true", "--server.port", str(PORT),
                         "--browser.gatherUsageStats", "false",
                         "--client.toolbarMode", "minimal"], cwd=HERE)
print("Avvio dell'interfaccia in corso...")
for _ in range(60):
    if proc.poll() is not None:
        sys.exit("L'interfaccia non è partita: controlla i messaggi qui sopra. "
                 "Hai installato le librerie? python -m pip install -r requirements.txt")
    try:
        urllib.request.urlopen(URL, timeout=1)
        break
    except Exception:
        time.sleep(0.5)
webbrowser.open(URL)
print(f"Interfaccia aperta nel browser: {URL}")
print("Lascia aperta questa finestra mentre la usi. Per chiudere l'interfaccia chiudi la finestra "
      "o premi Ctrl+C.")
try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
