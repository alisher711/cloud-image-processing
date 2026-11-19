"""RabbitMQ helpers (pika)."""
import json
import time

import pika

from . import config


def _parameters():
    s = config.rabbit_settings()
    return pika.ConnectionParameters(
        host=s["host"],
        port=s["port"],
        credentials=pika.PlainCredentials(s["user"], s["password"]),
        heartbeat=600,
        blocked_connection_timeout=300,
        socket_timeout=5,
        connection_attempts=1,
    )


def connect(retries=30, delay=2.0):
    last = None
    for attempt in range(retries):
        try:
            return pika.BlockingConnection(_parameters())
        except (pika.exceptions.AMQPError, OSError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(delay)
    raise last


def declare(channel):
    channel.queue_declare(queue=config.QUEUE_NAME, durable=True)


def publish_job(job_id):
    conn = connect(retries=3, delay=1.0)
    try:
        ch = conn.channel()
        declare(ch)
        ch.confirm_delivery()  # raises if the broker does not accept the message
        ch.basic_publish(
            exchange="",
            routing_key=config.QUEUE_NAME,
            body=json.dumps({"job_id": job_id}),
            properties=pika.BasicProperties(
                delivery_mode=2, content_type="application/json"  # persistent
            ),
        )
    finally:
        conn.close()


def queue_length():
    conn = connect(retries=1)
    try:
        ch = conn.channel()
        result = ch.queue_declare(queue=config.QUEUE_NAME, durable=True)
        return result.method.message_count
    finally:
        conn.close()


def ping():
    conn = connect(retries=1)
    conn.close()