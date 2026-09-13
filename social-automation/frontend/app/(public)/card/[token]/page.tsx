'use client'

import { useEffect, useState } from 'react'
import {
  Mail,
  Phone,
  Globe,
  MapPin,
  MessageCircle,
  Share2,
  Download,
  Copy,
  Check,
  Facebook,
  Instagram,
  Linkedin,
  Twitter,
  Music2,
} from 'lucide-react'

interface SocialLink {
  platform: string
  handle: string
  url: string | null
  display_name: string | null
}

interface CardData {
  name: string
  title: string | null
  company: string | null
  tagline: string | null
  description: string | null
  email: string | null
  phone: string | null
  website: string | null
  address: string | null
  primary_color: string | null
  accent_color: string | null
  logo_url: string | null
  avatar_url: string | null
  social_links: SocialLink[]
  services: { title: string; description: string }[]
  card_url: string | null
  vcard: string | null
}

const platformIcons: Record<string, typeof Facebook> = {
  facebook: Facebook,
  instagram: Instagram,
  linkedin: Linkedin,
  twitter: Twitter,
  tiktok: Music2,
  threads: MessageCircle,
  whatsapp: MessageCircle,
}

const platformLabels: Record<string, string> = {
  facebook: 'Facebook',
  instagram: 'Instagram',
  linkedin: 'LinkedIn',
  twitter: 'Twitter/X',
  tiktok: 'TikTok',
  threads: 'Threads',
  whatsapp: 'WhatsApp',
}

export default function DigitalCardPage({ params }: { params: { token: string } }) {
  const [card, setCard] = useState<CardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8083'}/api/v1/digital-cards/public/${params.token}`)
      .then((res) => {
        if (!res.ok) throw new Error('Card not found')
        return res.json()
      })
      .then((data) => {
        setCard(data)
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [params.token])

  const downloadVCard = () => {
    if (!card?.vcard) return
    const blob = new Blob([card.vcard], { type: 'text/vcard;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${card.name.replace(/\s+/g, '_')}.vcf`
    a.click()
    URL.revokeObjectURL(url)
    // Track save
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8083'}/api/v1/digital-cards/public/${params.token}/track?action=save`, { method: 'POST' }).catch(() => {})
  }

  const copyLink = () => {
    if (!card?.card_url) return
    navigator.clipboard.writeText(card.card_url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const shareViaWhatsApp = () => {
    if (!card) return
    const text = encodeURIComponent(
      `${card.name} — ${card.tagline || ''}\n${card.card_url || ''}`,
    )
    window.open(`https://wa.me/?text=${text}`, '_blank')
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8083'}/api/v1/digital-cards/public/${params.token}/track?action=share`, { method: 'POST' }).catch(() => {})
  }

  const shareViaMessenger = () => {
    if (!card?.card_url) return
    window.open(
      `https://www.facebook.com/dialog/send?link=${encodeURIComponent(card.card_url)}&app_id=1048494182856834&redirect_uri=${encodeURIComponent(window.location.href)}`,
      '_blank',
    )
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8083'}/api/v1/digital-cards/public/${params.token}/track?action=share`, { method: 'POST' }).catch(() => {})
  }

  const shareViaNative = async () => {
    if (!card) return
    if (navigator.share) {
      try {
        await navigator.share({
          title: card.name,
          text: `${card.name} — ${card.tagline || ''}`,
          url: card.card_url || window.location.href,
        })
      } catch {
        // user cancelled
      }
    } else {
      copyLink()
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0b1220]">
        <div className="text-[#00fff5] text-lg animate-pulse">Loading digital card…</div>
      </div>
    )
  }

  if (error || !card) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0b1220]">
        <div className="text-center">
          <div className="text-red-400 text-lg mb-2">Card not found</div>
          <div className="text-slate-400 text-sm">{error || 'Invalid share token'}</div>
        </div>
      </div>
    )
  }

  const primary = card.primary_color || '#0b1220'
  const accent = card.accent_color || '#00fff5'

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ backgroundColor: primary }}
    >
      <div
        className="w-full max-w-md rounded-3xl shadow-2xl overflow-hidden"
        style={{
          background: `linear-gradient(145deg, ${primary} 0%, #0f172b 60%, #1e293b 100%)`,
          border: `1px solid ${accent}33`,
        }}
      >
        {/* Header / Logo area */}
        <div className="relative px-8 pt-10 pb-6 text-center">
          {/* Glow effect */}
          <div
            className="absolute top-0 left-1/2 -translate-x-1/2 w-40 h-40 rounded-full blur-3xl opacity-20"
            style={{ backgroundColor: accent }}
          />
          {/* Logo or initials */}
          {card.logo_url || card.avatar_url ? (
            <img
              src={card.logo_url || card.avatar_url || ''}
              alt={card.name}
              className="relative w-24 h-24 mx-auto rounded-2xl object-cover mb-4"
              style={{ border: `2px solid ${accent}44` }}
            />
          ) : (
            <div
              className="relative w-24 h-24 mx-auto rounded-2xl flex items-center justify-center mb-4"
              style={{
                background: `linear-gradient(135deg, ${accent}22, ${accent}05)`,
                border: `2px solid ${accent}44`,
              }}
            >
              <span
                className="text-4xl font-bold"
                style={{ color: accent, fontFamily: 'Instrument Sans, sans-serif' }}
              >
                {card.name.charAt(0)}
              </span>
            </div>
          )}

          {/* Name */}
          <h1
            className="relative text-3xl font-bold tracking-tight"
            style={{ color: '#e2e8f0', fontFamily: 'Instrument Sans, sans-serif' }}
          >
            {card.name}
          </h1>

          {/* Title */}
          {card.title && (
            <p className="relative mt-1 text-sm text-slate-400">{card.title}</p>
          )}

          {/* Tagline */}
          {card.tagline && (
            <p
              className="relative mt-2 text-sm font-medium"
              style={{ color: accent }}
            >
              {card.tagline}
            </p>
          )}
        </div>

        {/* Description */}
        {card.description && (
          <div className="px-8 pb-4">
            <p className="text-sm text-slate-300 leading-relaxed text-center">
              {card.description.length > 180
                ? `${card.description.substring(0, 180)}…`
                : card.description}
            </p>
          </div>
        )}

        {/* Contact buttons */}
        <div className="px-8 py-4 space-y-2">
          {card.phone && (
            <a
              href={`tel:${card.phone.replace(/\s/g, '')}`}
              className="flex items-center gap-3 w-full px-4 py-3 rounded-xl transition-all hover:scale-[1.02]"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              <Phone size={18} style={{ color: accent }} />
              <span className="text-sm text-slate-200">{card.phone}</span>
            </a>
          )}
          {card.email && (
            <a
              href={`mailto:${card.email}`}
              className="flex items-center gap-3 w-full px-4 py-3 rounded-xl transition-all hover:scale-[1.02]"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              <Mail size={18} style={{ color: accent }} />
              <span className="text-sm text-slate-200">{card.email}</span>
            </a>
          )}
          {card.website && (
            <a
              href={card.website}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 w-full px-4 py-3 rounded-xl transition-all hover:scale-[1.02]"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              <Globe size={18} style={{ color: accent }} />
              <span className="text-sm text-slate-200">{card.website.replace(/^https?:\/\//, '')}</span>
            </a>
          )}
          {card.address && (
            <div
              className="flex items-center gap-3 w-full px-4 py-3 rounded-xl"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              <MapPin size={18} style={{ color: accent }} />
              <span className="text-sm text-slate-200">{card.address}</span>
            </div>
          )}
        </div>

        {/* Social links */}
        {card.social_links.length > 0 && (
          <div className="px-8 py-4">
            <div className="flex flex-wrap gap-2 justify-center">
              {card.social_links.map((s, i) => {
                const Icon = platformIcons[s.platform] || Globe
                return s.url ? (
                  <a
                    key={i}
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 px-3 py-2 rounded-lg transition-all hover:scale-105"
                    style={{
                      background: `${accent}0a`,
                      border: `1px solid ${accent}22`,
                    }}
                    title={platformLabels[s.platform] || s.platform}
                  >
                    <Icon size={16} style={{ color: accent }} />
                    <span className="text-xs text-slate-300">
                      {platformLabels[s.platform] || s.platform}
                    </span>
                  </a>
                ) : null
              })}
            </div>
          </div>
        )}

        {/* Services */}
        {card.services.length > 0 && (
          <div className="px-8 py-4">
            <div
              className="text-xs uppercase tracking-wider mb-3 text-center"
              style={{ color: `${accent}99` }}
            >
              What we do
            </div>
            <div className="space-y-2">
              {card.services.slice(0, 5).map((s, i) => (
                <div key={i} className="flex items-start gap-2">
                  <div
                    className="mt-1 w-1.5 h-1.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: accent }}
                  />
                  <div>
                    <span className="text-sm font-medium text-slate-200">{s.title}</span>
                    {s.description && (
                      <span className="text-xs text-slate-400 ml-1">— {s.description}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* QR Code */}
        {card.card_url && (
          <div className="px-8 py-6 flex flex-col items-center">
            <div
              className="p-3 rounded-2xl bg-white"
              style={{ border: `1px solid ${accent}33` }}
            >
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(card.card_url)}&color=0b1220&bgcolor=ffffff&margin=0`}
                alt="QR Code"
                width={160}
                height={160}
              />
            </div>
            <p className="mt-2 text-xs text-slate-500">Scan to open this card</p>
          </div>
        )}

        {/* Action buttons */}
        <div className="px-8 pb-8 space-y-2">
          {/* Primary share row */}
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={shareViaWhatsApp}
              className="flex items-center justify-center gap-2 px-4 py-3 rounded-xl font-medium text-sm transition-all hover:scale-[1.02]"
              style={{
                background: '#25D366',
                color: '#fff',
              }}
            >
              <MessageCircle size={18} />
              WhatsApp
            </button>
            <button
              onClick={shareViaMessenger}
              className="flex items-center justify-center gap-2 px-4 py-3 rounded-xl font-medium text-sm transition-all hover:scale-[1.02]"
              style={{
                background: '#0084FF',
                color: '#fff',
              }}
            >
              <MessageCircle size={18} />
              Messenger
            </button>
          </div>

          {/* Secondary actions */}
          <div className="grid grid-cols-3 gap-2">
            <button
              onClick={downloadVCard}
              className="flex flex-col items-center gap-1 px-2 py-3 rounded-xl transition-all hover:scale-[1.02]"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              <Download size={18} style={{ color: accent }} />
              <span className="text-xs text-slate-300">Save Contact</span>
            </button>
            <button
              onClick={shareViaNative}
              className="flex flex-col items-center gap-1 px-2 py-3 rounded-xl transition-all hover:scale-[1.02]"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              <Share2 size={18} style={{ color: accent }} />
              <span className="text-xs text-slate-300">Share</span>
            </button>
            <button
              onClick={copyLink}
              className="flex flex-col items-center gap-1 px-2 py-3 rounded-xl transition-all hover:scale-[1.02]"
              style={{
                background: `${accent}11`,
                border: `1px solid ${accent}22`,
              }}
            >
              {copied ? (
                <Check size={18} style={{ color: accent }} />
              ) : (
                <Copy size={18} style={{ color: accent }} />
              )}
              <span className="text-xs text-slate-300">
                {copied ? 'Copied!' : 'Copy Link'}
              </span>
            </button>
          </div>
        </div>

        {/* Footer */}
        <div
          className="px-8 py-4 text-center"
          style={{ borderTop: `1px solid ${accent}11` }}
        >
          <p className="text-xs text-slate-500">
            Powered by{' '}
            <span style={{ color: `${accent}99` }}>SocialAuto</span>
          </p>
        </div>
      </div>
    </div>
  )
}
