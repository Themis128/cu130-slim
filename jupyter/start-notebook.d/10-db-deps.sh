#!/usr/bin/env bash
# Install DB drivers the scipy-notebook image doesn't ship.
pip install --quiet --no-input sqlalchemy psycopg2-binary 2>/dev/null || true
