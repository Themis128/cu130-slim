#!/bin/bash
# Legacy start script (supervisord is the primary entrypoint now)
set -e
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
