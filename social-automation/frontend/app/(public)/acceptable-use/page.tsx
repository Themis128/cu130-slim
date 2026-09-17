import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Acceptable Use Policy — SocialAuto by Cloudless',
  description: 'Acceptable use policy for SocialAuto, the social media automation platform by Cloudless.',
  robots: { index: true, follow: true },
}

export default function AcceptableUsePage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Acceptable Use Policy</h1>
      <p className="text-muted-foreground mb-8">Last updated: September 17, 2026</p>

      <div className="prose prose-slate dark:prose-invert max-w-none space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-semibold mb-2">1. Purpose</h2>
          <p>
            This Acceptable Use Policy ("AUP") defines what is and is not permitted on SocialAuto,
            operated by Cloudless. It protects our users, the social platforms we integrate with,
            and the public. Violations may result in content removal, suspension, or account
            termination under our <a href="/terms" className="text-blue-500 hover:underline">Terms of Service</a>.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">2. Automation Rules</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Automation acts only on social accounts you own or are authorized to manage, and only after you explicitly connect them.</li>
            <li>DM auto-reply is <strong>opt-in and inbound-only</strong>. It may only respond to messages sent to you. Unsolicited outbound DMs, mass messaging, and cold outreach are prohibited.</li>
            <li>You must comply with each platform's own automation and rate-limit rules (Meta, Instagram, LinkedIn, X, TikTok, Threads, WhatsApp).</li>
            <li>Built-in safeguards — per-account toggles, rate limits, quiet hours, and human-takeover — must not be circumvented.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">3. Prohibited Content and Activity</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Spam, unsolicited bulk posting, engagement bait, or coordinated inauthentic behavior.</li>
            <li>Illegal, fraudulent, deceptive, or misleading content, including undisclosed AI impersonation of real people.</li>
            <li>Hate speech, harassment, threats, or incitement to violence.</li>
            <li>Malware, phishing, credential harvesting, or attempts to compromise accounts or systems.</li>
            <li>Intellectual-property infringement, including publishing content you have no right to use.</li>
            <li>Circumventing platform enforcement — e.g. evading bans, creating sockpuppet networks, or buying fake engagement.</li>
            <li>Reselling or sharing account access, or using the Service to operate accounts you do not control.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">4. Platform Compliance</h2>
          <p>
            You are responsible for ensuring your use of connected social accounts complies with
            each network's terms. If a platform suspends your account for automation you
            configured, that is a third-party enforcement decision — but if we determine your use
            violates this AUP, we may suspend your SocialAuto access as well.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">5. Enforcement</h2>
          <p>
            We may review accounts and content for AUP compliance, including in response to
            reports from platforms, payment partners, or users. Enforcement actions include
            warnings, feature restriction, suspension, and termination. Suspected fraud or
            intentional abuse may be blocked immediately.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">6. Reporting</h2>
          <p>
            Report violations to <a href="mailto:support@cloudless.gr" className="text-blue-500 hover:underline">support@cloudless.gr</a>.
            We acknowledge reports within 48 hours.
          </p>
        </section>
      </div>
    </div>
  )
}
