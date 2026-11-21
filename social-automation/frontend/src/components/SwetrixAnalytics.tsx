'use client'

import Script from 'next/script'

/**
 * Swetrix CE — self-hosted, cookieless analytics (analytics.cloudless.gr).
 * Mounted on public marketing pages only (pricing/features funnel surface);
 * the dashboard app is excluded on purpose.
 *
 * Project id is public by design (it ships in the page source like any
 * analytics id); NEXT_PUBLIC_SWETRIX_PROJECT_ID overrides it when set.
 */
const DEFAULT_PROJECT_ID = 'jncLHvg1XBKD' // social.cloudless.gr
const API_URL = 'https://analytics.cloudless.gr/backend/v1/log'

export function SwetrixAnalytics() {
  const projectId = process.env.NEXT_PUBLIC_SWETRIX_PROJECT_ID || DEFAULT_PROJECT_ID

  return (
    <>
      <Script src="https://swetrix.org/swetrix.js" strategy="lazyOnload" />
      <Script id="swetrix-init" strategy="lazyOnload">{`
        document.addEventListener("DOMContentLoaded", function () {
          if (typeof swetrix === "undefined") return;
          swetrix.init(${JSON.stringify(projectId)}, { apiURL: ${JSON.stringify(API_URL)} });
          swetrix.trackViews();
        });
      `}</Script>
    </>
  )
}
