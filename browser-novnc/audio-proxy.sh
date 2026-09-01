#!/bin/bash
# Audio proxy: captures PulseAudio output and encodes to WebM/Opus for
# WebSocket streaming via websockify.
#
# PulseAudio listens on port 8001 (raw s16le PCM), GStreamer encodes to
# WebM/Opus and outputs to port 8002, websockify wraps port 8002 as
# WebSocket on port 6081.

set -e

AUDIO_TCP_PORT=8001
OPUS_TCP_PORT=8002
AUDIO_WS_PORT=6081

# Start GStreamer: raw PCM -> Opus/WebM
# GStreamer reads from the PulseAudio TCP port and outputs WebM/Opus
gst-launch-1.0 -q \
  tcpclientsrc host=127.0.0.1 port=${AUDIO_TCP_PORT} \
  ! audio/x-raw,format=S16LE,channels=2,rate=48000 \
  ! opusenc bitrate=128000 \
  ! webmmux \
  ! tcpserversink host=127.0.0.1 port=${OPUS_TCP_PORT} &

sleep 1

# Start websockify to wrap the Opus stream as a WebSocket
websockify ${AUDIO_WS_PORT} 127.0.0.1:${OPUS_TCP_PORT} &

echo "Audio proxy running: WebSocket on port ${AUDIO_WS_PORT}"

wait
