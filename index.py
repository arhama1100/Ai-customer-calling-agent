"""Vercel WSGI entry point. Importing the app does not connect or seed a DB."""

from app import create_app

app = create_app()
