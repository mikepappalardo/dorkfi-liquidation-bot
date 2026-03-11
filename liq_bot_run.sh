#!/bin/bash
cd /Users/michaelpappalardo/.openclaw/workspace
source liq-bot.env 2>/dev/null
python3 liquidation_bot.py >> liq_bot_output.log 2>&1
