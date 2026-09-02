'use client'

interface ToneSlidersProps {
  dimensions?: Record<string, number>
  onChange: (dimensions: Record<string, number>) => void
}

export const TONE_DIMENSIONS = [
  { key: 'formality', label: 'Formality', leftLabel: 'Casual', rightLabel: 'Formal' },
  { key: 'playfulness', label: 'Playfulness', leftLabel: 'Serious', rightLabel: 'Playful' },
  { key: 'authority', label: 'Authority', leftLabel: 'Humble', rightLabel: 'Authoritative' },
  { key: 'friendliness', label: 'Friendliness', leftLabel: 'Distant', rightLabel: 'Friendly' },
  { key: 'technical', label: 'Technical Depth', leftLabel: 'Simple', rightLabel: 'Technical' },
]

export function ToneSliders({ dimensions = {}, onChange }: ToneSlidersProps) {
  return (
    <div className="space-y-6">
      {TONE_DIMENSIONS.map((dim) => (
        <div key={dim.key} className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{dim.leftLabel}</span>
            <span className="font-medium">{dim.label}: {dimensions[dim.key] ?? 3}</span>
            <span className="text-muted-foreground">{dim.rightLabel}</span>
          </div>
          <input
            type="range"
            min={1}
            max={5}
            step={1}
            value={dimensions[dim.key] ?? 3}
            onChange={(e) => onChange({ ...dimensions, [dim.key]: parseInt(e.target.value) })}
            className="w-full accent-primary"
            aria-label={dim.label}
          />
        </div>
      ))}
    </div>
  )
}
