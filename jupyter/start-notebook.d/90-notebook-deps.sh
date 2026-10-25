#!/bin/bash
# Agent notebooks deps — papermill for scheduled runs, ipython-sql for %sql.
pip install --quiet papermill ipython-sql psycopg2-binary pandas 2>/dev/null || true
