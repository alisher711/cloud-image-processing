import os

QUEUE_NAME = "image_jobs"


def _env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def data_dir():
    return _env("DATA_DIR", "/data")


def db_kwargs():
    return {
        "host": _env("POSTGRES_HOST", "postgres"),
        "port": int(_env("PG_PORT_NUMBER", "5432")),
        "dbname": _env("POSTGRES_DB", "images"),
        "user": _env("POSTGRES_USER", "imageuser"),
        "password": _env("POSTGRES_PASSWORD"),
    }


def rabbit_settings():
    return {
        "host": _env("RABBITMQ_HOST", "rabbitmq"),
        "port": int(_env("AMQP_PORT_NUMBER", "5672")),
        "user": _env("RABBITMQ_USER", "imageuser"),
        "password": _env("RABBITMQ_PASSWORD"),
    }


def max_upload_bytes():
    return int(_env("MAX_UPLOAD_MB", "10")) * 1024 * 1024


def max_attempts():
    return int(_env("MAX_ATTEMPTS", "3"))


def processing_delay():
    return float(_env("PROCESSING_DELAY_SECONDS", "0"))