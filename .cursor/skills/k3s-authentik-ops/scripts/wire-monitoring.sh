#!/bin/bash
kubectl -n monitoring get pods 2>/dev/null | head -4
echo "scrape 100.74.191.58:9300/metrics; alert on authentik_failed_logins_total"
