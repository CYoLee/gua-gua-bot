import os
from threading import Thread
from flask import Flask
from redeem_bot import start_discord_bot

app = Flask(__name__)


@app.route("/")
def index():
    return "✅ GuaGuaBOT 正常啟動中"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    Thread(target=run_flask).start()
    start_discord_bot()
