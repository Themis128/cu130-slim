#!/bin/bash
H=${1:-100.84.31.85}; shift
exec ssh -o BatchMode=yes -o StrictHostKeyChecking=no -p 8022 "$H" "${@:-true}"
