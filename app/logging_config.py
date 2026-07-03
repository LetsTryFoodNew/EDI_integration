from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(environment: str = "local") -> None:
    """Set up structured JSON logging via structlog."""

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if environment == "local":
        # Human-readable in dev
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        # JSON in staging/prod
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    # Quiet noisy libraries
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if environment == "local" else logging.WARNING
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
