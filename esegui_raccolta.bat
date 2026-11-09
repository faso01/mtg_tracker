@echo off
REM Lancia la raccolta giornaliera (CardTrader + Scryfall) e salva l'output in collector.log
REM nella stessa cartella di questo file. Da usare con l'Utilita' di pianificazione.
cd /d "%~dp0"
echo ===== %date% %time% CardTrader ===== >> collector.log
python ct_collector.py >> collector.log 2>&1
echo ===== fine CardTrader, codice %errorlevel% ===== >> collector.log
echo ===== %date% %time% Scryfall ===== >> collector.log
python scryfall_sync.py >> collector.log 2>&1
echo ===== fine Scryfall, codice %errorlevel% ===== >> collector.log
