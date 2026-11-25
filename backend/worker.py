"""Cloudflare Python Worker entrypoint.

Keep the application package at the project root so absolute imports
resolve in the Workers runtime.
"""

from app.worker import Default
