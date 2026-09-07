"""Unit tests for plan quota / limit helpers in app.core.quotas."""

from app.core.quotas import PLAN_LIMITS, get_effective_limit, get_plan_limits


class TestGetPlanLimits:
    def test_free_tier(self) -> None:
        limits = get_plan_limits("free")
        assert limits["posts_per_month"] == 10
        assert limits["ai_calls_per_month"] == 50
        assert limits["social_accounts"] == 1

    def test_pro_tier(self) -> None:
        limits = get_plan_limits("pro")
        assert limits["posts_per_month"] == 100
        assert limits["ai_calls_per_month"] == 500
        assert limits["social_accounts"] == 5

    def test_business_tier(self) -> None:
        limits = get_plan_limits("business")
        assert limits["posts_per_month"] == -1  # unlimited
        assert limits["ai_calls_per_month"] == 5000
        assert limits["social_accounts"] == 20

    def test_enterprise_tier(self) -> None:
        limits = get_plan_limits("enterprise")
        assert limits["posts_per_month"] == -1
        assert limits["ai_calls_per_month"] == -1
        assert limits["social_accounts"] == -1

    def test_unknown_tier_defaults_to_free(self) -> None:
        limits = get_plan_limits("nonexistent")
        assert limits == PLAN_LIMITS["free"]

    def test_empty_string_defaults_to_free(self) -> None:
        limits = get_plan_limits("")
        assert limits == PLAN_LIMITS["free"]


class TestGetEffectiveLimit:
    def test_free_posts(self) -> None:
        assert get_effective_limit("free", "posts_per_month") == 10

    def test_free_ai_calls(self) -> None:
        assert get_effective_limit("free", "ai_calls_per_month") == 50

    def test_free_social_accounts(self) -> None:
        assert get_effective_limit("free", "social_accounts") == 1

    def test_business_unlimited_posts(self) -> None:
        assert get_effective_limit("business", "posts_per_month") == -1

    def test_enterprise_all_unlimited(self) -> None:
        for resource in ("posts_per_month", "ai_calls_per_month", "social_accounts"):
            assert get_effective_limit("enterprise", resource) == -1

    def test_unknown_tier_defaults_to_free(self) -> None:
        assert get_effective_limit("gold", "posts_per_month") == 10
        assert get_effective_limit("gold", "ai_calls_per_month") == 50

    def test_unknown_resource_returns_negative_one(self) -> None:
        assert get_effective_limit("free", "nonexistent_resource") == -1
