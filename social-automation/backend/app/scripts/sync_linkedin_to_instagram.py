"""Sync LinkedIn Company Page profile to Instagram.

Reads the LinkedIn Company Page (cloudless-gr) bio and website via the
browser sidecar, then updates the Instagram profile via the instagrapi
private API client.

Usage:
    docker compose exec -T social-api python -m app.scripts.sync_linkedin_to_instagram
"""
import asyncio
import logging

from app.core.config import get_settings
from app.services.instagrapi_client import InstagrapiClient, InstagrapiError
from app.services.linkedin_sidecar import LinkedInSidecarClient, LinkedInSidecarError
from app.services.secret_store import secret_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

LINKEDIN_VANITY = "cloudless-gr"


async def read_linkedin_company(client: LinkedInSidecarClient) -> dict:
    """Read the LinkedIn Company Page profile."""
    logger.info("Reading LinkedIn Company Page: %s", LINKEDIN_VANITY)
    try:
        result = await client.get_company(LINKEDIN_VANITY)
        company = result.get("company", {})

        # If the sidecar didn't parse the about/website, navigate and scrape
        if not company.get("about") or not company.get("website"):
            logger.info("Sidecar returned partial data — navigating to about page directly")
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as http:
                # Use the debug endpoints to read the about page
                await http.post(
                    f"{client.base_url}/debug/navigate",
                    json={"url": f"https://www.linkedin.com/company/{LINKEDIN_VANITY}/about/?viewAsMember=true"},
                    timeout=30.0,
                )
                await asyncio.sleep(4)
                resp = await http.post(
                    f"{client.base_url}/debug/eval",
                    json={"script": "document.body.innerText.substring(0, 5000)"},
                    timeout=15.0,
                )
                text = resp.json().get("result", "")

                # Parse the about text
                about_start = text.find("Overview")
                if about_start == -1:
                    about_start = text.find("☁️")
                if about_start >= 0:
                    about_text = text[about_start:]
                    # Find the website URL
                    import re

                    url_match = re.search(r"https?://[^\s,)]+", about_text)
                    website = url_match.group(0) if url_match else None

                    # Extract the tagline (first line after the company name)
                    tagline_match = re.search(r"(?:☁️|⚡|🚀|🔥).*?(?=\n|$)", about_text)
                    tagline = tagline_match.group(0) if tagline_match else None

                    company["about"] = about_text[:2000]
                    if website:
                        company["website"] = website
                    if tagline:
                        company["tagline"] = tagline

        logger.info(
            "LinkedIn: name=%s, website=%s, about=%s...",
            company.get("name"),
            company.get("website"),
            str(company.get("about", ""))[:80],
        )
        return company
    except LinkedInSidecarError as exc:
        logger.error("LinkedIn sidecar error: %s", exc)
        raise


def build_instagram_bio(linkedin_company: dict) -> str:
    """Build a concise Instagram bio from LinkedIn Company Page data.

    Instagram bios have a 150-character limit, so we keep it concise.
    """
    about = linkedin_company.get("about", "")

    if about:
        lines = [line.strip() for line in about.split("\n") if line.strip()]
        # Skip section headers like "Overview"
        content_lines = [line for line in lines if line.lower() not in ("overview", "about", "about us", "")]

        # Find the first content line (usually starts with emoji or is the tagline)
        for line in content_lines:
            # Skip empty or header lines
            if len(line) < 5:
                continue
            # This is the tagline/first bio line
            if len(line) > 150:
                return line[:147] + "..."
            return line

    # Fallback: use the current Instagram bio format
    return "☁️ Clear skies. Zero friction.\n⚡ Cloud • Serverless • AI Marketing\n🚀 Startups & SMBs\n📍 Greece | 🌍 Worldwide"


async def get_instagram_credentials() -> tuple[str, str]:
    """Get Instagram credentials from the secret store."""
    username = await secret_store.get("INSTAGRAM_USERNAME") or get_settings().INSTAGRAM_USERNAME
    password = await secret_store.get("INSTAGRAM_PASSWORD") or get_settings().INSTAGRAM_PASSWORD
    if not username or not password:
        raise ValueError("Instagram credentials not found in secret store or .env")
    return username, password


async def main() -> None:
    logger.info("=== LinkedIn → Instagram Profile Sync ===")

    # 1. Read LinkedIn Company Page
    linkedin_client = LinkedInSidecarClient()
    linkedin_company = await read_linkedin_company(linkedin_client)

    # 2. Build the Instagram bio from LinkedIn data
    new_bio = build_instagram_bio(linkedin_company)
    new_website = linkedin_company.get("website", "https://cloudless.gr")

    logger.info("Prepared Instagram bio: %s", new_bio)
    logger.info("Prepared Instagram website: %s", new_website)

    # 3. Get Instagram credentials
    try:
        ig_username, ig_password = await get_instagram_credentials()
    except ValueError as exc:
        logger.error("Cannot sync: %s", exc)
        return

    # 4. Update Instagram profile
    logger.info("Updating Instagram profile for @%s", ig_username)
    ig_client = InstagrapiClient(
        username=ig_username,
        password=ig_password,
        proxy=get_settings().INSTAGRAM_PROXY,
    )

    try:
        result = await ig_client.update_profile(
            biography=new_bio,
            external_url=new_website,
        )
        logger.info("Instagram profile updated: %s", result)
        print("\n✅ Sync complete!")
        print(f"   LinkedIn → Instagram bio: {new_bio[:80]}...")
        print(f"   LinkedIn → Instagram website: {new_website}")
    except InstagrapiError as exc:
        logger.error("Instagram update failed: %s", exc)
        print(f"\n❌ Sync failed: {exc}")
        print("   The Instagram private API may be rate-limited.")
        print("   Try again later or use the browser bridge.")


if __name__ == "__main__":
    asyncio.run(main())
