#!/bin/zsh
# 獨立執行入口：定時任務與雙擊都靠它，不依賴任何 AI 服務或特定工作目錄。
cd "$(dirname "$0")"
exec .venv/bin/python main.py "$@"
