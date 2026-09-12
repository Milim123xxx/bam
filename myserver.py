import os
from threading import Thread

from flask import Flask

app = Flask(__name__)


@app.get("/")
def home():
    return "Discord bot is running!", 200


@app.get("/health")
def health():
    return {"status": "ok"}, 200


def run() -> None:
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def server_on() -> None:
    thread = Thread(target=run, name="health-server", daemon=True)
    thread.start()
