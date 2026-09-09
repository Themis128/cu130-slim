"""Unit tests for content_scorer service."""
from app.services.content_scorer import (
    ContentScore,
    HashtagStrategy,
    build_hashtag_strategy,
    score_content,
)


class TestScoreContent:
    def test_empty_content(self):
        score = score_content("", "linkedin")
        assert score.readability == 0.0
        assert score.engagement == 0.0
        assert score.length_fit == 0.0

    def test_basic_content(self):
        text = "The cat sat on the mat. It was a good day."
        score = score_content(text, "linkedin")
        assert 0 < score.readability <= 100
        assert 0 < score.engagement <= 100
        assert 0 < score.length_fit <= 100

    def test_engagement_with_question(self):
        text = "Did you know serverless can save you 50% on cloud costs? Learn more!"
        score = score_content(text, "linkedin")
        assert score.engagement > 60  # Question + CTA + emotional + numbers + exclam

    def test_engagement_with_cta(self):
        text = "Click the link to learn more about our service."
        score = score_content(text, "linkedin")
        assert score.engagement > 60

    def test_engagement_with_emotional_words(self):
        text = "This is an amazing incredible breakthrough that will transform your workflow."
        score = score_content(text, "linkedin")
        assert score.engagement > 60

    def test_length_fit_optimal(self):
        # 50-90% of 3000 chars for linkedin
        text = "a" * 2000
        score = score_content(text, "linkedin")
        assert score.length_fit == 100.0

    def test_length_fit_short(self):
        text = "a" * 100
        score = score_content(text, "linkedin")
        assert score.length_fit < 80

    def test_length_fit_over_limit(self):
        text = "a" * 500  # over threads 500 limit
        score = score_content(text, "threads")
        assert score.length_fit < 50

    def test_hashtag_quality_good(self):
        tags = ["#cloud", "#serverless", "#startup"]
        score = score_content("Some content", "linkedin", tags)
        assert score.hashtag_quality > 60

    def test_hashtag_quality_too_many(self):
        tags = [f"#tag{i}" for i in range(20)]
        score = score_content("Some content", "linkedin", tags)
        assert score.hashtag_quality < 80

    def test_hashtag_quality_empty(self):
        score = score_content("Some content", "linkedin", [])
        assert score.hashtag_quality == 50.0

    def test_overall_score(self):
        text = "Serverless cloud architecture is amazing. Learn more today!"
        tags = ["#cloud", "#serverless"]
        score = score_content(text, "linkedin", tags)
        assert 0 < score.overall <= 100

    def test_to_dict(self):
        score = ContentScore(
            readability=80.0, engagement=70.0, hashtag_quality=60.0, length_fit=90.0
        )
        d = score.to_dict()
        assert "readability" in d
        assert "engagement" in d
        assert "hashtag_quality" in d
        assert "length_fit" in d
        assert "overall" in d
        assert d["overall"] > 0

    def test_platform_limits(self):
        # Twitter has 280 char limit
        text = "a" * 200
        score = score_content(text, "twitter")
        assert score.length_fit == 100.0  # 200/280 = 71% (in optimal range)


class TestBuildHashtagStrategy:
    def test_basic_topic(self):
        strategy = build_hashtag_strategy("serverless cloud architecture", "linkedin")
        assert len(strategy.safe) > 0
        assert len(strategy.rising) > 0
        assert "#serverless" in strategy.safe

    def test_existing_tags(self):
        strategy = build_hashtag_strategy(
            "cloud computing", "instagram", existing_tags=["#customtag"]
        )
        assert "#customtag" in strategy.all_tags

    def test_platform_limits(self):
        strategy = build_hashtag_strategy(
            "serverless cloud architecture devops", "twitter"
        )
        # Twitter total is 3
        assert len(strategy.all_tags) <= 3

    def test_to_dict(self):
        strategy = HashtagStrategy(
            safe=["#a"], rising=["#b"], niche=["#c"]
        )
        d = strategy.to_dict()
        assert d["safe"] == ["#a"]
        assert d["rising"] == ["#b"]
        assert d["niche"] == ["#c"]

    def test_all_tags(self):
        strategy = HashtagStrategy(
            safe=["#a"], rising=["#b"], niche=["#c", "#d"]
        )
        assert strategy.all_tags == ["#a", "#b", "#c", "#d"]
