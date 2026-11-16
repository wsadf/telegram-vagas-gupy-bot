@echo off
:: Roda o bot na pasta onde este arquivo está (Python 3.8+ instalado)
cd /d "%~dp0"
echo. >> erro_log.txt
echo ======================================== >> erro_log.txt
echo Data/Hora: %date% %time% >> erro_log.txt
echo ======================================== >> erro_log.txt
set PYTHONIOENCODING=utf-8
python main.py >> erro_log.txt 2>&1
echo Fim da execucao em: %time% >> erro_log.txt
