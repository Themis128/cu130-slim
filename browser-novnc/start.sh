#!/bin/bash
# Container entrypoint — clean stale X11 locks before supervisord starts.
# A crashed container leaves /tmp/.X99-lock behind, which makes Xvfb exit
# immediately and supervisord mark it FATAL after retries.
set -e
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
