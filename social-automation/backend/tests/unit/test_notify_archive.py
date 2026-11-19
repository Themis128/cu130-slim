"""Unit tests for the notification-archive collector + incident classifier.

Covers the Slack/ntfy surfaces landed in the all-channels archive work:
id encoding (bigint-safe), channel discovery, and _classify target rules.
"""

import os
from unittest.mock import patch

from app.worker.tasks import notify_archive as NA
from app.worker.tasks.telegram_incidents import _classify

SIGNED_BIGINT_MAX = 0x7FFFFFFFFFFFFFFF


# ---- id encodings -----------------------------------------------------

def test_slack_msg_id_microseconds():
    assert NA._slack_msg_id("1795087811.752809") == 1795087811752809


def test_slack_msg_id_pads_short_fraction():
    assert NA._slack_msg_id("1795087811.75") == 1795087811750000


def test_slack_msg_id_fits_signed_bigint():
    for ts in ("9999999999.999999", "1795087811.752809", "1.1"):
        assert 0 <= NA._slack_msg_id(ts) <= SIGNED_BIGINT_MAX


def test_ntfy_msg_id_is_stable_and_signed():
    a = NA._ntfy_msg_id("rlRnVLI9G6hi", 1700000000)
    b = NA._ntfy_msg_id("rlRnVLI9G6hi", 1700000000)
    assert a == b
    assert 0 <= a <= SIGNED_BIGINT_MAX


def test_ntfy_msg_id_differs_per_message_same_second():
    a = NA._ntfy_msg_id("rlRnVLI9G6hi", 1700000000)
    b = NA._ntfy_msg_id("XwA53rVRYaZ4", 1700000000)
    assert a != b  # same-second messages must not collide


def test_ntfy_msg_id_falls_back_to_time():
    assert NA._ntfy_msg_id("", 1700000000) == 1700000000


# ---- slack channel discovery ------------------------------------------

def test_slack_channel_ids_explicit_override():
    with patch.dict(os.environ, {"SLACK_ARCHIVE_CHANNELS": "C111,C222"}, clear=False):
        assert NA._slack_channel_ids() == ["C111", "C222"]


def test_slack_channel_ids_env_defaults_dedupe():
    env = {
        "SLACK_ARCHIVE_CHANNELS": "",
        "SLACK_ALERTS_CHANNEL_ID": "C0AAA",
        "SLACK_OPS_CHANNEL": "C0BBB",
        "SLACK_CHANNEL_ID": "C0AAA",  # dup of alerts
        "SLACK_TELEGRAM_CHANNEL_ID": "C0CCC",
    }
    with patch.dict(os.environ, env, clear=False):
        ids = NA._slack_channel_ids()
    assert ids == ["C0AAA", "C0BBB", "C0CCC"]


def test_slack_channel_ids_ignores_non_ids():
    with patch.dict(os.environ, {"SLACK_OPS_CHANNEL": "#ops-alerts", "SLACK_CHANNEL_ID": ""}, clear=False):
        assert NA._slack_channel_ids() == []


# ---- incident classifier ----------------------------------------------

def test_classify_container_name_target():
    kind, target = _classify("ops", "social-api unhealthy - restart loop detected")
    assert kind == "container_unhealthy"
    assert target == "social-api"


def test_classify_backtick_task_target():
    kind, target = _classify(
        "ntfy:cloudless-alerts",
        "[ops] cloudless\n:warning: Worker task failed `app.worker.tasks.notify_archive.notify_archive_ingest`",
    )
    assert kind == "infra_alert"
    assert target == "notify_archive.notify_archive_ingest"


def test_classify_emoji_shortcode_not_target():
    kind, target = _classify("slack:ops-alerts", ":warning: Worker task failed `app.worker.tasks.log_intel.digest`")
    assert target == "log_intel.digest"  # not ":warning:"


def test_classify_severity_prefix_stripped():
    kind, target = _classify("ntfy:nextcloud-ops", "[critical] nextcloud cron failed on omv")
    assert kind == "infra_alert"
    assert target == "nextcloud"  # not "[critical]" nor "cloudless"


def test_classify_generic_tokens_skipped():
    kind, target = _classify("ntfy:cloudless-ops", "cloudless disk pressure on omv - 92% full")
    assert kind == "disk_pressure"
    assert target != "cloudless"  # generic word skipped for a real token


def test_classify_non_incident_ignored():
    assert _classify("ntfy:cloudless-ops", "canary ok - cloudless-ops") is None
    assert _classify("ops", "all services recovered, healthy again") is None
    assert _classify("ops", "") is None


def test_classify_kind_mapping():
    assert _classify("ops", "disk full on omv")[0] == "disk_pressure"
    assert _classify("ops", "worker OOM killed")[0] == "oom_kill"
    assert _classify("ops", "tunnel returns 530")[0] == "tunnel_down"
    assert _classify("ops", "token expired for meta")[0] == "credential_expired"
