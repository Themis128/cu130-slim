import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Data Deletion Policy — SocialAuto by Cloudless',
  description: 'How to request data deletion from SocialAuto. Required by Meta App Review.',
  robots: { index: true, follow: true },
}

export default function DataDeletionPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Data Deletion Policy</h1>
      <p className="text-muted-foreground mb-8">Last updated: September 11, 2026</p>

      <div className="prose prose-slate dark:prose-invert max-w-none space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-semibold mb-2">Overview</h2>
          <p>
            SocialAuto respects your right to delete your data. This policy explains how you can
            request deletion of your personal data from our platform, in compliance with Meta's
            Platform Policies and GDPR.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">How to Request Data Deletion</h2>
          <p>You can request data deletion in three ways:</p>
          <ol className="list-decimal pl-6 space-y-2 mt-2">
            <li>
              <strong>In-app:</strong> Log in at
              <a href="https://social.cloudless.gr" className="text-blue-500 hover:underline"> social.cloudless.gr</a>,
              go to Settings → Account → Delete Account. This immediately removes all your data.
            </li>
            <li>
              <strong>Email:</strong> Send a deletion request to
              <a href="mailto:privacy@cloudless.gr" className="text-blue-500 hover:underline"> privacy@cloudless.gr</a>
              with the subject "Data Deletion Request". Include your registered email address.
            </li>
            <li>
              <strong>Meta Data Deletion Callback:</strong> When you remove the app from your
              Facebook/Instagram settings, Meta sends a deletion request to our callback endpoint
              automatically. We process it within 24 hours.
            </li>
          </ol>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">What Gets Deleted</h2>
          <p>When you request data deletion, we remove:</p>
          <ul className="list-disc pl-6 space-y-1">
            <li>Your user account and authentication credentials</li>
            <li>All connected social media accounts and OAuth tokens (revoked immediately)</li>
            <li>All posts, media, and content created through the platform</li>
            <li>All Messenger conversations and auto-reply configurations</li>
            <li>All analytics data and usage logs</li>
            <li>All AI usage history and generated content</li>
            <li>Team memberships and associated data</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">What We Keep (Legal Requirements)</h2>
          <p>We may retain limited data for legal compliance:</p>
          <ul className="list-disc pl-6 space-y-1">
            <li>Audit logs for 90 days (for security and fraud prevention)</li>
            <li>Financial records as required by tax law (up to 5 years)</li>
            <li>Data required to respond to legal requests</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">Timeline</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li><strong>In-app deletion:</strong> Immediate (within seconds)</li>
            <li><strong>Email request:</strong> Processed within 48 hours</li>
            <li><strong>Meta callback:</strong> Processed within 24 hours</li>
            <li><strong>Complete removal:</strong> All backups purged within 30 days</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">Data Deletion Callback</h2>
          <p>
            Our Meta data deletion callback endpoint is:
          </p>
          <pre className="bg-muted p-3 rounded text-xs overflow-x-auto">
            <code>POST https://social.cloudless.gr/api/v1/auth/data-deletion</code>
          </pre>
          <p>
            When a user removes the app from their Facebook/Instagram settings, Meta sends a signed
            request to this endpoint. We verify the signature, identify the user, and delete all
            their data. We return a confirmation code that Meta displays to the user.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">Contact</h2>
          <p>For data deletion questions:</p>
          <ul className="list-none pl-6 space-y-1">
            <li>Email: <a href="mailto:privacy@cloudless.gr" className="text-blue-500 hover:underline">privacy@cloudless.gr</a></li>
            <li>Website: <a href="https://cloudless.gr" className="text-blue-500 hover:underline">cloudless.gr</a></li>
          </ul>
        </section>
      </div>
    </div>
  )
}
