const platforms = [
  { name: 'LinkedIn', abbr: 'in' },
  { name: 'Facebook', abbr: 'f' },
  { name: 'Instagram', abbr: 'IG' },
  { name: 'Twitter / X', abbr: 'X' },
  { name: 'TikTok', abbr: 'TT' },
  { name: 'Threads', abbr: '@' },
  { name: 'Messenger', abbr: 'M' },
  { name: 'Telegram', abbr: 'TG' },
]

export function PlatformSupport() {
  return (
    <section className="border-y border-border bg-muted/30 py-16">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            Publish to every platform that matters
          </h2>
          <p className="mt-3 text-muted-foreground">
            Connect six social networks with secure OAuth, plus Messenger and
            Telegram — all published from one unified composer.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8">
          {platforms.map((p) => (
            <div
              key={p.name}
              className="flex flex-col items-center gap-3 rounded-xl border border-border bg-card p-6 text-center"
            >
              <span className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                {p.abbr}
              </span>
              <span className="text-sm font-medium text-foreground">
                {p.name}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
