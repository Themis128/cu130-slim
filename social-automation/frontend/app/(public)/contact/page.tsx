import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Contact — SocialAuto by Cloudless',
  description: 'How to reach SocialAuto support and the Cloudless team.',
  robots: { index: true, follow: true },
}

export default function ContactPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Contact Us</h1>
      <p className="text-muted-foreground mb-8">We respond to all inquiries within 48 hours.</p>

      <div className="prose prose-slate dark:prose-invert max-w-none space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-semibold mb-2">Support</h2>
          <ul className="list-none pl-6 space-y-1">
            <li>Email: <a href="mailto:support@cloudless.gr" className="text-blue-500 hover:underline">support@cloudless.gr</a> — monitored business hours, Europe/Athens</li>
            <li>Billing &amp; refunds: <a href="mailto:billing@cloudless.gr" className="text-blue-500 hover:underline">billing@cloudless.gr</a> — or use Settings → Billing → Customer Portal</li>
            <li>Privacy &amp; data: <a href="mailto:privacy@cloudless.gr" className="text-blue-500 hover:underline">privacy@cloudless.gr</a> — see also our <a href="/data-deletion" className="text-blue-500 hover:underline">Data Deletion Policy</a></li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">Company</h2>
          <p>
            SocialAuto is operated by Cloudless, headquartered in Greece.
          </p>
          <ul className="list-none pl-6 space-y-1">
            <li>Website: <a href="https://cloudless.gr" className="text-blue-500 hover:underline">cloudless.gr</a></li>
            <li>General inquiries: <a href="mailto:hello@cloudless.gr" className="text-blue-500 hover:underline">hello@cloudless.gr</a></li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-2">Billing Disputes</h2>
          <p>
            Payments are processed by our Merchant of Record (Polar Software, Inc. or Dodo
            Payments). Before filing a chargeback, please contact
            <a href="mailto:billing@cloudless.gr" className="text-blue-500 hover:underline"> billing@cloudless.gr</a> —
            we resolve billing issues faster directly than through your card issuer, per our
            <a href="/refund" className="text-blue-500 hover:underline"> Refund &amp; Cancellation Policy</a>.
          </p>
        </section>
      </div>
    </div>
  )
}
