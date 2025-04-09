# main.py
import os
import sys

mode = os.getenv("BOT_MODE", "api")

if mode == "discord":
    import redeem_bot
elif mode == "api":
    import flask_app
else:
    sys.exit(f"❌ Unknown BOT_MODE: {mode}")
