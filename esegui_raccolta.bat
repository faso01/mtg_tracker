@echo off
REM Lancia la raccolta giornaliera e salva l'output in collector.log
REM (nella stessa cartella di questo file). Da usare con l'Utilita' di pianificazione.
cd /d "%~dp0"
echo ===== %date% %time% ===== >> collector.log
python ct_collector.py >> collector.log 2>&1
echo ===== fine, codice %errorlevel% ===== >> collector.log
