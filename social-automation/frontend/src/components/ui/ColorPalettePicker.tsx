'use client'

import { Plus, X } from 'lucide-react'
import { Button } from './Button'
import { Input } from './Input'

interface ColorPalettePickerProps {
  primary?: string
  accent?: string
  neutrals?: string[]
  onChange: (colors: { primary?: string; accent?: string; neutrals?: string[] }) => void
}

const SWATCHES = [
  '#0A0A0F', '#1A1A2E', '#00FFF5', '#FF00FF', '#00FF41',
  '#4D7CFF', '#FF6B6B', '#FFD93D', '#6BCB77', '#4D96FF',
  '#F472B6', '#A78BFA', '#FB923C', '#34D399', '#60A5FA',
]

export function ColorPalettePicker({ primary, accent, neutrals = [], onChange }: ColorPalettePickerProps) {
  const update = (field: 'primary' | 'accent', value: string) => onChange({ [field]: value } as any)
  const addNeutral = (color: string) => {
    if (!neutrals.includes(color)) onChange({ neutrals: [...neutrals, color] })
  }
  const removeNeutral = (idx: number) => onChange({ neutrals: neutrals.filter((_, i) => i !== idx) })

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <ColorField label="Primary" value={primary} onChange={(v) => update('primary', v)} />
        <ColorField label="Accent" value={accent} onChange={(v) => update('accent', v)} />
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">Neutral Colors</label>
        <div className="flex flex-wrap gap-2 mb-2">
          {neutrals.map((c, i) => (
            <div key={i} className="relative group">
              <div className="h-10 w-10 rounded-lg border" style={{ backgroundColor: c }} />
              <button
                onClick={() => removeNeutral(i)}
                className="absolute -top-1 -right-1 bg-destructive text-destructive-foreground rounded-full p-0.5 opacity-0 group-hover:opacity-100 transition"
                aria-label="Remove color"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
          {neutrals.length === 0 && <span className="text-sm text-muted-foreground">No neutral colors</span>}
        </div>
      </div>

      <div>
        <label className="text-sm font-medium mb-2 block">Quick Pick</label>
        <div className="flex flex-wrap gap-2">
          {SWATCHES.map((c) => (
            <button
              key={c}
              onClick={() => addNeutral(c)}
              className="h-8 w-8 rounded-lg border hover:scale-110 transition"
              style={{ backgroundColor: c }}
              aria-label={`Add ${c}`}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function ColorField({ label, value, onChange }: { label: string; value?: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="text-sm font-medium mb-2 block">{label}</label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={value || '#000000'}
          onChange={(e) => onChange(e.target.value)}
          className="h-10 w-12 rounded border cursor-pointer"
          aria-label={`${label} color picker`}
        />
        <Input
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#000000"
          className="flex-1"
        />
      </div>
    </div>
  )
}
