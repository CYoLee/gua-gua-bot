# main.py
import threading
from flask_app import start_flask_app
from redeem_bot import start_discord_bot

if __name__ == "__main__":
    threading.Thread(target=start_flask_app).start()
    start_discord_bot()
