"""Optional Celery worker for distributed, horizontally-scalable runs.

Importing this package never requires Celery. The Celery app and tasks are only
constructed when the ``queue`` extra is installed and a broker is configured;
otherwise the platform runs investigations in-process via the CLI/API.
"""

from __future__ import annotations
