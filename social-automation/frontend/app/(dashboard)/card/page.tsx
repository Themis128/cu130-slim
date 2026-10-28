'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  Contact,
  Plus,
  Sparkles,
  Trash2,
  Download,
  Copy,
  Check,
  ExternalLink,
  Send,
  Eye,
  Share2,
  MessageCircle,
  PhoneCall,
  Loader2,
} from 'lucide-react'
import { digitalCardApi } from '@/services/api'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'

interface SocialLink {
  platform: string
  handle: string
  url: string | null
  display_name: string | null
}

interface DigitalCardData {
  id: string
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
  share_token: string
  is_active: boolean
  view_count: number
  contact_save_count: number
  share_count: number
  card_url: string | null
  vcard: string | null
  created_at: string | null
}

export default function DigitalCardPage() {
  const router = useRouter()
  const [cards, setCards] = useState<DigitalCardData[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [sendModal, setSendModal] = useState<DigitalCardData | null>(null)
  const [sendPhone, setSendPhone] = useState('')
  const [sendPlatform, setSendPlatform] = useState('whatsapp')
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState<string | null>(null)

  const loadCards = useCallback(async () => {
    try {
      const res = await digitalCardApi.list()
      setCards(res.data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err?.response?.data?.detail || 'Failed to load cards')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadCards()
  }, [loadCards])

  const createFromBrand = async () => {
    setCreating(true)
    setError(null)
    try {
      await digitalCardApi.createFromBrand()
      await loadCards()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err?.response?.data?.detail || 'Failed to create card from brand')
    } finally {
      setCreating(false)
    }
  }

  const deleteCard = async (id: string) => {
    if (!confirm('Delete this digital card? This cannot be undone.')) return
    try {
      await digitalCardApi.delete(id)
      setCards(cards.filter((c) => c.id !== id))
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err?.response?.data?.detail || 'Failed to delete card')
    }
  }

  const copyLink = (card: DigitalCardData) => {
    if (!card.card_url) return
    navigator.clipboard.writeText(card.card_url)
    setCopiedId(card.id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  const downloadVCard = async (card: DigitalCardData) => {
    try {
      const res = await digitalCardApi.downloadVCard(card.id)
      const blob = new Blob([res.data], { type: 'text/vcard' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${card.name.replace(/\s+/g, '_')}.vcf`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // fallback: use the vcard text from the card object
      if (card.vcard) {
        const blob = new Blob([card.vcard], { type: 'text/vcard' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${card.name.replace(/\s+/g, '_')}.vcf`
        a.click()
        URL.revokeObjectURL(url)
      }
    }
  }

  const openSendModal = (card: DigitalCardData) => {
    setSendModal(card)
    setSendPhone('')
    setSendPlatform('whatsapp')
    setSendResult(null)
  }

  const sendCard = async () => {
    if (!sendModal) return
    setSending(true)
    setSendResult(null)
    try {
      const res = await digitalCardApi.send(sendModal.id, {
        to_phone: sendPhone,
        platform: sendPlatform,
      })
      if (res.data.success) {
        setSendResult(`Sent successfully! Message ID: ${res.data.message_id || 'ok'}`)
      } else {
        setSendResult(`Failed: ${res.data.error || 'Unknown error'}`)
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setSendResult(`Error: ${err?.response?.data?.detail || 'Failed to send'}`)
    } finally {
      setSending(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Contact className="h-7 w-7" />
            Digital Business Cards
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Create vCard 4.0 compliant digital cards and share via WhatsApp or Messenger
          </p>
        </div>
        <Button onClick={createFromBrand} disabled={creating}>
          {creating ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4 mr-2" />
          )}
          Create from Brand
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-500">
          {error}
        </div>
      )}

      {/* Empty state */}
      {cards.length === 0 && !loading && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <Contact className="h-16 w-16 text-muted-foreground/40 mb-4" />
            <h3 className="text-lg font-semibold mb-2">No digital cards yet</h3>
            <p className="text-sm text-muted-foreground mb-6 text-center max-w-md">
              Create a digital business card from your Cloudless brand identity.
              It will include your contact info, social links, services, and a QR code
              for sharing via WhatsApp and Messenger.
            </p>
            <Button onClick={createFromBrand} disabled={creating}>
              {creating ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4 mr-2" />
              )}
              Generate from Brand
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Card grid */}
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <Card key={card.id} className="overflow-hidden">
            {/* Card preview header */}
            <div
              className="px-6 py-5"
              style={{
                background: `linear-gradient(145deg, ${card.primary_color || '#0b1220'} 0%, #1e293b 100%)`,
              }}
            >
              <div className="flex items-center gap-3">
                {card.logo_url || card.avatar_url ? (
                  <img
                    src={card.logo_url || card.avatar_url || ''}
                    alt={card.name}
                    className="w-12 h-12 rounded-xl object-cover"
                    style={{ border: `1px solid ${card.accent_color || '#00fff5'}44` }}
                  />
                ) : (
                  <div
                    className="w-12 h-12 rounded-xl flex items-center justify-center"
                    style={{
                      background: `${card.accent_color || '#00fff5'}11`,
                      border: `1px solid ${card.accent_color || '#00fff5'}44`,
                    }}
                  >
                    <span
                      className="text-xl font-bold"
                      style={{ color: card.accent_color || '#00fff5' }}
                    >
                      {card.name.charAt(0)}
                    </span>
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-white truncate">{card.name}</h3>
                  {card.tagline && (
                    <p
                      className="text-xs truncate"
                      style={{ color: card.accent_color || '#00fff5' }}
                    >
                      {card.tagline}
                    </p>
                  )}
                </div>
              </div>
            </div>

            <CardContent className="p-4 space-y-3">
              {/* Stats */}
              <div className="flex gap-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Eye className="h-3 w-3" /> {card.view_count} views
                </span>
                <span className="flex items-center gap-1">
                  <Download className="h-3 w-3" /> {card.contact_save_count} saves
                </span>
                <span className="flex items-center gap-1">
                  <Share2 className="h-3 w-3" /> {card.share_count} shares
                </span>
              </div>

              {/* Contact info */}
              {card.email && (
                <p className="text-xs text-muted-foreground truncate">{card.email}</p>
              )}
              {card.phone && (
                <p className="text-xs text-muted-foreground truncate">{card.phone}</p>
              )}

              {/* Social links */}
              {card.social_links.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {card.social_links.slice(0, 5).map((s, i) => (
                    <Badge key={i} variant="secondary" className="text-xs">
                      {s.platform}
                    </Badge>
                  ))}
                </div>
              )}

              {/* Actions */}
              <div className="grid grid-cols-2 gap-2 pt-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => copyLink(card)}
                >
                  {copiedId === card.id ? (
                    <Check className="h-3 w-3 mr-1" />
                  ) : (
                    <Copy className="h-3 w-3 mr-1" />
                  )}
                  Copy Link
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => downloadVCard(card)}
                >
                  <Download className="h-3 w-3 mr-1" />
                  vCard
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => openSendModal(card)}
                >
                  <Send className="h-3 w-3 mr-1" />
                  Send
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => card.card_url && window.open(card.card_url, '_blank')}
                >
                  <ExternalLink className="h-3 w-3 mr-1" />
                  View
                </Button>
              </div>

              {/* Delete */}
              <Button
                size="sm"
                variant="ghost"
                className="w-full text-red-500 hover:text-red-600"
                onClick={() => deleteCard(card.id)}
              >
                <Trash2 className="h-3 w-3 mr-1" />
                Delete
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Send modal */}
      {sendModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <Card className="w-full max-w-md">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Send className="h-5 w-5" />
                Send &ldquo;{sendModal.name}&rdquo; Card
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="text-sm font-medium mb-2 block">Platform</label>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant={sendPlatform === 'whatsapp' ? 'default' : 'outline'}
                    onClick={() => setSendPlatform('whatsapp')}
                  >
                    <MessageCircle className="h-4 w-4 mr-1" />
                    WhatsApp
                  </Button>
                  <Button
                    size="sm"
                    variant={sendPlatform === 'messenger' ? 'default' : 'outline'}
                    onClick={() => setSendPlatform('messenger')}
                  >
                    <MessageCircle className="h-4 w-4 mr-1" />
                    Messenger
                  </Button>
                </div>
              </div>
              <div>
                <label className="text-sm font-medium mb-2 block">
                  {sendPlatform === 'whatsapp' ? 'Phone number (E.164)' : 'Recipient PSID'}
                </label>
                <Input
                  type="text"
                  placeholder={sendPlatform === 'whatsapp' ? '+30 697 777 7838' : '1234567890123456'}
                  value={sendPhone}
                  onChange={(e) => setSendPhone(e.target.value)}
                />
              </div>
              {sendResult && (
                <div
                  className={`rounded-lg p-3 text-sm ${
                    sendResult.startsWith('Sent')
                      ? 'border border-green-500/30 bg-green-500/10 text-green-600'
                      : 'border border-red-500/30 bg-red-500/10 text-red-500'
                  }`}
                >
                  {sendResult}
                </div>
              )}
              <div className="flex gap-2 justify-end">
                <Button variant="outline" onClick={() => setSendModal(null)}>
                  Cancel
                </Button>
                <Button onClick={sendCard} disabled={sending || !sendPhone}>
                  {sending ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4 mr-2" />
                  )}
                  Send Card
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
