from fastapi import APIRouter

from app.api import (
    accounts,
    agency,
    ai,
    ai_providers,
    analytics,
    audit,
    auth,
    billing,
    bluesky,
    brand,
    cf_db,
    coach,
    content,
    digital_cards,
    email_campaigns,
    engagement,
    funnel,
    inbox,
    instagram,
    leads,
    linkedin,
    mcp,
    media,
    media_enhance,
    media_sources,
    messenger,
    meta_growth,
    newsletter,
    ops,
    profile,
    publishing,
    reports,
    research,
    secrets,
    service_feed,
    support,
    talk_bot,
    teams,
    telegram,
    threads,
    tiktok,
    twitter_tiktok,
    usage,
    viber,
    web_analytics,
    whatsapp,
    whatsapp_flows,
    workflows,
    x_webhook,
)

api_router = APIRouter()
router = api_router  # alias — some includes use bare router


router.include_router(auth.router, prefix="/auth", tags=["auth"])

router.include_router(billing.router, prefix="/billing", tags=["billing"])

router.include_router(content.router, prefix="/content", tags=["content"])

router.include_router(coach.router, prefix="/coach", tags=["coach"])

router.include_router(media.router, prefix="/media", tags=["media"])

router.include_router(media_enhance.router, prefix="/media/enhance", tags=["media-enhance"])

router.include_router(meta_growth.router, prefix="/meta-growth", tags=["meta-growth"])

router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])

router.include_router(research.router, prefix="/research", tags=["research"])
router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])

router.include_router(linkedin.router, prefix="/linkedin", tags=["linkedin"])

router.include_router(instagram.router, prefix="/instagram", tags=["instagram"])

router.include_router(threads.router, prefix="/threads", tags=["threads"])

router.include_router(tiktok.router, prefix="/tiktok", tags=["tiktok"])
router.include_router(x_webhook.router, tags=["webhooks"])

router.include_router(publishing.router, prefix="/publishing", tags=["publishing"])

router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])

router.include_router(ai.router, prefix="/ai", tags=["ai"])

router.include_router(ai_providers.router, prefix="/ai-providers", tags=["ai-providers"])

router.include_router(brand.router, prefix="/brand", tags=["brand"])

router.include_router(ops.router, prefix="/ops", tags=["ops"])

router.include_router(support.router, prefix="/support", tags=["support"])

router.include_router(talk_bot.router, prefix="/nextcloud", tags=["talk-bot"])

router.include_router(cf_db.router, prefix="/cf-db", tags=["cf-db"])

router.include_router(profile.router, prefix="/profile", tags=["profile"])

router.include_router(messenger.router, prefix="/messenger", tags=["messenger"])

router.include_router(inbox.router, prefix="/inbox", tags=["inbox"])

router.include_router(funnel.router, prefix="/funnel", tags=["funnel"])

router.include_router(leads.router, prefix="/leads", tags=["leads"])

router.include_router(email_campaigns.router, prefix="/email-campaigns", tags=["email-campaigns"])

router.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])

router.include_router(whatsapp_flows.router, prefix="/whatsapp", tags=["whatsapp-flows"])

router.include_router(telegram.router, prefix="/telegram", tags=["telegram"])

router.include_router(newsletter.router, prefix="/newsletter", tags=["newsletter"])

router.include_router(viber.router, prefix="/viber", tags=["viber"])

router.include_router(bluesky.router, prefix="/bluesky", tags=["bluesky"])

router.include_router(secrets.router, prefix="/secrets", tags=["secrets"])

router.include_router(audit.router, prefix="/audit", tags=["audit"])

router.include_router(agency.router, prefix="/agency", tags=["agency"])

router.include_router(mcp.router, prefix="/mcp", tags=["mcp"])

router.include_router(teams.router, prefix="/teams", tags=["teams"])

router.include_router(twitter_tiktok.router, tags=["twitter-tiktok-dm"])

router.include_router(usage.router, prefix="/usage", tags=["usage"])

router.include_router(digital_cards.router, prefix="/digital-cards", tags=["digital-cards"])

router.include_router(reports.router, prefix="/reports", tags=["reports"])

router.include_router(service_feed.router, prefix="/service-feed", tags=["service-feed"])

router.include_router(web_analytics.router, prefix="/analytics/web", tags=["web-analytics"])

router.include_router(engagement.router)

router.include_router(media_sources.router, prefix="/media-sources", tags=["media-sources"])

from app.api import (  # noqa: E402 — registered late to avoid import cycles
    ads,
    x_webhook,  # noqa: E402
)

api_router.include_router(ads.router)
