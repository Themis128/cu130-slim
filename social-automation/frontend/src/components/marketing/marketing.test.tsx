import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PublicNav } from './PublicNav'
import { PublicFooter } from './PublicFooter'
import { Hero } from './Hero'
import { CTASection } from './CTASection'
import { FeatureGrid } from './FeatureGrid'
import { PlatformSupport } from './PlatformSupport'
import { FunnelSection } from './FunnelSection'
import { PricingTable } from './PricingTable'
import { SafeImage } from '@/components/SafeImage'
import { SafeVideo } from '@/components/SafeVideo'

describe('PublicNav', () => {
  it('renders nav links and toggles the mobile menu', () => {
    render(<PublicNav />)
    expect(screen.getByText('SocialAuto')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Pricing' })).toHaveAttribute('href', '/pricing')
    expect(screen.getByRole('link', { name: 'Sign In' })).toHaveAttribute('href', '/login')

    const toggle = screen.getByLabelText('Toggle menu')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    // mobile menu renders its own nav links
    expect(screen.getAllByRole('link', { name: 'Features' }).length).toBeGreaterThan(1)
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('PublicNav mobile menu links', () => {
  it('closes the menu when a mobile link or CTA is chosen', () => {
    render(<PublicNav />)
    const toggle = screen.getByLabelText('Toggle menu')

    fireEvent.click(toggle)
    const features = screen.getAllByRole('link', { name: 'Features' })
    fireEvent.click(features[features.length - 1])
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)
    const signIn = screen.getAllByRole('link', { name: 'Sign In' })
    fireEvent.click(signIn[signIn.length - 1])
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)
    const getStarted = screen.getAllByRole('link', { name: 'Get Started' })
    fireEvent.click(getStarted[getStarted.length - 1])
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('marketing sections', () => {
  it('renders the hero and CTA sections', () => {
    const { container: h } = render(<Hero />)
    expect(h.textContent!.length).toBeGreaterThan(20)
    const { container: c } = render(<CTASection />)
    expect(c.textContent!.length).toBeGreaterThan(10)
  })

  it('renders features, platform support and funnel sections', () => {
    const { container: f } = render(<FeatureGrid />)
    expect(f.querySelectorAll('a, h3, [class*="card"]').length).toBeGreaterThan(0)
    const { container: p } = render(<PlatformSupport />)
    expect(p.textContent!.length).toBeGreaterThan(10)
    const { container: fn } = render(<FunnelSection />)
    expect(fn.textContent!.length).toBeGreaterThan(10)
  })

  it('renders the pricing table and footer', () => {
    const { container: pt } = render(<PricingTable />)
    expect(pt.textContent!.length).toBeGreaterThan(10)
    const { container: pf } = render(<PublicFooter />)
    expect(pf.querySelectorAll('a').length).toBeGreaterThan(0)
  })
})

describe('Safe media components', () => {
  it('SafeImage renders only for safe URLs', () => {
    const { container, rerender } = render(<SafeImage src="https://x/a.png" alt="a" width={10} height={10} />)
    expect(screen.getByAltText('a')).toHaveAttribute('src', 'https://x/a.png')

    rerender(<SafeImage src="javascript:alert(1)" alt="a" />)
    expect(screen.queryByAltText('a')).toBeNull()
    rerender(<SafeImage src={null} alt="a" />)
    expect(container.firstChild).toBeNull()
  })

  it('SafeVideo renders only for safe URLs', () => {
    const { container, rerender } = render(<SafeVideo src="blob:http://x/v" muted autoPlay playsInline />)
    expect(container.querySelector('video')).not.toBeNull()

    rerender(<SafeVideo src="javascript:alert(1)" />)
    expect(container.querySelector('video')).toBeNull()
  })
})
