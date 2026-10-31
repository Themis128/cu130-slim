from app.worker.celery_app import celery_app
from app.worker.tasks import instagram_token_refresh as m


def test_threshold_exceeds_beat_interval():
    sched = celery_app.conf.beat_schedule["refresh-instagram-tokens"]["schedule"]
    # crontab daily -> interval 1 day; threshold must leave >1 day of margin
    assert getattr(sched, "hour", None) is not None
    assert m.REFRESH_THRESHOLD_DAYS >= 7
