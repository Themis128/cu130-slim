import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Refund & Cancellation Policy — SocialAuto by Cloudless',
  description: 'How to cancel your SocialAuto subscription and request a refund.',
  robots: { index: true, follow: true },
}

export default function RefundPolicyPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Refund &amp; Cancellation Policy</h1>
      <p className="text-muted-foreground mb-8">Last updated: September 17, 2026</p>

      <div className="prose prose-slate dark:prose-invert max-w-none space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-semibold mb-2">1. Cancelling Your Subscription</h2>
          <p>
            You can cancel your SocialAuto subscription at any time, with no cancellation fee:
          </p>
          <ul className="list-disc pl-6 space-y-1">
            <li><strong>In-app:</strong> Settings → Billing → Cancel subscription.</li>
            <li><strong>Customer portal:</strong> Use the hosted billing portal linked from Settings → Billing to manage or cancel your plan directly.</li>
            <li><strong>Email:</strong> Contact <a href="mailto:support@cloudless.gr" className="text-blue-500 hover:underline">support@cloudless.gr</a> from your account email.</li>
          </ul>
          <p className="mt-2">
            Cancellation takes effect at the end of your current billing period — you keep full
            access until then. We do not charge for the following period after cancellation.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">2. Refund Eligibility</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li><strong>First 14 days:</strong> If the Service does not work as described, contact us within 14 days of your first payment for a full refund.</li>
            <li><strong>Service outages:</strong> If a defect on our side prevents use of the Service for an extended period, we will refund or credit the affected period.</li>
            <li><strong>Accidental or duplicate charges:</strong> Contact us and we will refund them.</li>
            <li><strong>Statutory rights:</strong> EU/EEA consumers retain any mandatory withdrawal and refund rights under applicable consumer-protection law.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">3. What Is Not Refundable</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Partial-month use after the first 14 days, except where required by law or covered by Section 2.</li>
            <li>Accounts suspended or terminated for violations of our <a href="/acceptable-use" className="text-blue-500 hover:underline">Acceptable Use Policy</a> or <a href="/terms" className="text-blue-500 hover:underline">Terms of Service</a>.</li>
            <li>Third-party platform actions outside our control (e.g. a social network restricting your account).</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">4. How Refunds Are Processed</h2>
          <p>
            Payments are processed by our Merchant of Record (Polar Software, Inc. or Dodo
            Payments). Approved refunds are issued to the original payment method, typically
            within 5–10 business days depending on your bank or card issuer. You never need to
            dispute a charge to get help — contact us first and we will resolve it.
          </p>
          <p>
            Note: charges on your bank or card statement appear as
            <strong> POLAR*CLOUDLESS</strong> or <strong>DODOPAYMENTS*</strong> — these are our
            payment partners processing your SocialAuto subscription.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">5. Contact</h2>
          <ul className="list-none pl-6 space-y-1">
            <li>Email: <a href="mailto:support@cloudless.gr" className="text-blue-500 hover:underline">support@cloudless.gr</a> (we reply within 48 hours)</li>
            <li>Website: <a href="https://cloudless.gr" className="text-blue-500 hover:underline">cloudless.gr</a></li>
          </ul>
        </section>
      </div>
    </div>
  )
}
