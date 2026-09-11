import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Privacy Policy — SocialAuto by Cloudless',
  description: 'Privacy policy for SocialAuto, the social media automation platform by Cloudless.',
  robots: { index: true, follow: true },
}

export default function PrivacyPolicyPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Privacy Policy</h1>
      <p className="text-muted-foreground mb-8">Last updated: September 11, 2026</p>

      <div className="prose prose-slate dark:prose-invert max-w-none space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-semibold mb-2">1. Overview</h2>
          <p>
            SocialAuto ("we", "us", "our") is a social media automation platform
            operated by Cloudless, headquartered in Greece. This privacy policy explains how we
            collect, use, and protect your data when you use our platform at
            <a href="https://social.cloudless.gr" className="text-blue-500 hover:underline"> social.cloudless.gr</a>.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">2. Data We Collect</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li><strong>Account information:</strong> Name, email address, and password (hashed) when you register.</li>
            <li><strong>OAuth tokens:</strong> Access tokens from connected social media accounts (Facebook, Instagram, LinkedIn, Twitter/X, TikTok, Threads) to manage your posts and messages.</li>
            <li><strong>Messenger data:</strong> When you connect Facebook Messenger, we receive and process incoming messages to generate AI-powered auto-replies on your behalf.</li>
            <li><strong>Content:</strong> Posts, media, and messages you create or receive through the platform.</li>
            <li><strong>Usage data:</strong> API requests, device type, and browser information for analytics and security.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">3. How We Use Your Data</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>To authenticate you and manage your connected social media accounts.</li>
            <li>To publish, schedule, and manage social media posts on your behalf.</li>
            <li>To receive and respond to Messenger messages using AI auto-reply.</li>
            <li>To provide analytics on your social media performance.</li>
            <li>To maintain and improve the platform's features and reliability.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">4. Messenger Platform</h2>
          <p>
            When you enable Messenger on your Facebook Page, our platform:
          </p>
          <ul className="list-disc pl-6 space-y-1">
            <li>Receives incoming messages via Facebook webhooks.</li>
            <li>Generates AI-powered replies using your brand voice and messaging guidelines.</li>
            <li>Sends replies within Facebook's 24-hour messaging window.</li>
            <li>Stores conversation history for your review in the Messenger inbox.</li>
          </ul>
          <p className="mt-2">
            We do not share message data with third parties. AI replies are generated using
            Cloudflare Workers AI (free tier) or a local self-hosted model (Docker Model Runner).
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">5. Data Storage</h2>
          <p>
            Your data is stored in:
          </p>
          <ul className="list-disc pl-6 space-y-1">
            <li><strong>Primary:</strong> Cloudflare D1 (edge database) and Cloudflare KV (cache).</li>
            <li><strong>Failover:</strong> Local PostgreSQL database on our infrastructure.</li>
            <li><strong>Media:</strong> Cloudflare R2 or local MinIO (S3-compatible storage).</li>
            <li><strong>OAuth tokens:</strong> Encrypted at rest using AES-256.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">6. Data Sharing</h2>
          <p>
            We do not sell or share your data with third parties. We only share data with:
          </p>
          <ul className="list-disc pl-6 space-y-1">
            <li>Facebook/Meta (to publish posts and manage Messenger, as directed by you).</li>
            <li>LinkedIn, Twitter/X, TikTok, Threads (to publish content, as directed by you).</li>
            <li>Cloudflare (for edge database, cache, and AI inference).</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">7. Your Rights</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li><strong>Access:</strong> You can view all your data through the platform.</li>
            <li><strong>Deletion:</strong> You can delete your account and all associated data at any time.</li>
            <li><strong>Disconnect:</strong> You can disconnect any social media account at any time.</li>
            <li><strong>Export:</strong> You can export your posts and media from the platform.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">8. Data Retention</h2>
          <p>
            We retain your data for as long as your account is active. When you delete your account,
            we remove all personal data within 30 days, except where required by law.
            OAuth tokens are revoked immediately upon disconnection.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">9. Security</h2>
          <p>
            We use industry-standard security measures:
          </p>
          <ul className="list-disc pl-6 space-y-1">
            <li>OAuth tokens encrypted at rest (AES-256).</li>
            <li>HTTPS/TLS for all connections.</li>
            <li>Cloudflare Tunnel for secure access (no open ports).</li>
            <li>Rate limiting and circuit breakers for API protection.</li>
            <li>Webhook signature verification for all incoming Meta events.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">10. Contact</h2>
          <p>
            For privacy questions or data requests, contact us at:
          </p>
          <ul className="list-none pl-6 space-y-1">
            <li>Email: <a href="mailto:privacy@cloudless.gr" className="text-blue-500 hover:underline">privacy@cloudless.gr</a></li>
            <li>Website: <a href="https://cloudless.gr" className="text-blue-500 hover:underline">cloudless.gr</a></li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">11. Updates</h2>
          <p>
            We may update this privacy policy from time to time. We will notify you of significant
            changes via email or in-app notification.
          </p>
        </section>
      </div>
    </div>
  )
}
