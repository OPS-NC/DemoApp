import os
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

import redis
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, jsonify, render_template

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
COUNTER_KEY = os.environ.get("COUNTER_KEY", "demoapp:views")
TZ_NAME = os.environ.get("TZ", "Pacific/Noumea")
COMMIT = os.environ.get("COMMIT", "unknown")

app = Flask(__name__)
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_host_info():
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        ip = "unavailable"
    return hostname, ip


def incr_views():
    try:
        return redis_client.incr(COUNTER_KEY)
    except redis.RedisError:
        return None


def get_views():
    try:
        return redis_client.get(COUNTER_KEY) or 0
    except redis.RedisError:
        return None


def get_time():
    try:
        tz = ZoneInfo(TZ_NAME)
    except (KeyError, ValueError):
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    return now.strftime("%H:%M:%S"), now.strftime("%A %d %B %Y"), str(tz), now.strftime("%z")


@app.route("/")
def index():
    hostname, ip = get_host_info()
    time_str, date_str, tz_label, offset = get_time()
    views = incr_views()
    return render_template(
        "index.html",
        time=time_str,
        date=date_str,
        timezone=tz_label,
        offset=offset,
        hostname=hostname,
        ip=ip,
        views=views,
        commit=COMMIT,
    )


@app.route("/api/info")
def api_info():
    """Lecture seule : ne compte pas comme une vue (utilisé par le polling front)."""
    hostname, ip = get_host_info()
    time_str, date_str, tz_label, offset = get_time()
    return jsonify(
        time=time_str,
        date=date_str,
        timezone=tz_label,
        offset=offset,
        hostname=hostname,
        ip=ip,
        views=get_views(),
        commit=COMMIT,
    )


@app.route("/healthz")
def healthz():
    return jsonify(status="ok"), 200


@app.route("/readyz")
def readyz():
    try:
        redis_client.ping()
    except redis.RedisError as exc:
        return jsonify(status="redis unavailable", detail=str(exc)), 503
    return jsonify(status="ok"), 200


asgi_app = WsgiToAsgi(app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
