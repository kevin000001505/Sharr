import atexit
import pika

from app.config import settings
from app.schemas import TransferJob

_connection = None
_channel = None


def _create_connection():
    credentials = pika.PlainCredentials(
        settings.rabbitmq_user, settings.rabbitmq_password
    )
    params = pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        credentials=credentials,
        heartbeat=0,
        blocked_connection_timeout=30,
    )
    return pika.BlockingConnection(params)


def _get_channel() -> pika.channel.Channel:
    global _channel, _connection
    if _channel is None or _channel.is_closed:
        if _connection is None or _connection.is_closed:
            _connection = _create_connection()
        _channel = _connection.channel()
        _channel.queue_declare(
            queue=settings.rabbitmq_queue, durable=True
        )
    return _channel


def _close_connection():
    global _channel, _connection
    if _channel and not _channel.is_closed:
        try:
            _channel.close()
        except Exception:
            pass
        _channel = None
    if _connection and not _connection.is_closed:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None


atexit.register(_close_connection)


def publish_job(job: TransferJob) -> None:
    channel = _get_channel()
    channel.basic_publish(
        exchange="",
        routing_key=settings.rabbitmq_queue,
        body=job.model_dump_json(),
        properties=pika.BasicProperties(delivery_mode=2),
    )


def consume_jobs(handler):
    channel = _get_channel()

    def callback(ch, method, properties, body):
        job = TransferJob.model_validate_json(body)
        handler(job)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(
        queue=settings.rabbitmq_queue,
        on_message_callback=callback,
    )
    channel.start_consuming()
