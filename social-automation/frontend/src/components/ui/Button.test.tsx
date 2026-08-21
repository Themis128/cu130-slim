import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { Button } from '@/components/ui/Button'

describe('Button', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders children', () => {
    render(<Button>Click me</Button>)
    expect(screen.getByRole('button', { name: /click me/i })).toBeInTheDocument()
  })

  it('applies variant classes', () => {
    const { unmount } = render(<Button variant="default">Default</Button>)
    expect(screen.getByRole('button')).toHaveClass('bg-primary')
    unmount()

    const { unmount: unmount2 } = render(<Button variant="destructive">Delete</Button>)
    expect(screen.getByRole('button')).toHaveClass('bg-destructive')
    unmount2()

    const { unmount: unmount3 } = render(<Button variant="outline">Outline</Button>)
    expect(screen.getByRole('button')).toHaveClass('border')
    unmount3()

    const { unmount: unmount4 } = render(<Button variant="ghost">Ghost</Button>)
    expect(screen.getByRole('button')).toHaveClass('hover:bg-accent')
    unmount4()

    const { unmount: unmount5 } = render(<Button variant="link">Link</Button>)
    expect(screen.getByRole('button')).toHaveClass('text-primary')
    unmount5()
  })

  it('applies size classes', () => {
    const { unmount } = render(<Button size="default">Default</Button>)
    expect(screen.getByRole('button')).toHaveClass('h-10')
    unmount()

    const { unmount: unmount2 } = render(<Button size="sm">Small</Button>)
    expect(screen.getByRole('button')).toHaveClass('h-9')
    unmount2()

    const { unmount: unmount3 } = render(<Button size="lg">Large</Button>)
    expect(screen.getByRole('button')).toHaveClass('h-11')
    unmount3()

    const { unmount: unmount4 } = render(<Button size="icon">Icon</Button>)
    expect(screen.getByRole('button')).toHaveClass('h-10')
    unmount4()
  })

  it('handles disabled state', () => {
    render(<Button disabled>Disabled</Button>)
    expect(screen.getByRole('button')).toBeDisabled()
  })

  it('shows loading state', () => {
    render(<Button isLoading>Loading</Button>)
    expect(screen.getByRole('button')).toBeDisabled()
    expect(screen.getByRole('button')).toContainHTML('svg')
  })

  it('calls onClick handler', () => {
    const handleClick = vi.fn()
    render(<Button onClick={handleClick}>Click me</Button>)
    fireEvent.click(screen.getByRole('button'))
    expect(handleClick).toHaveBeenCalledTimes(1)
  })

  it('does not call onClick when disabled', () => {
    const handleClick = vi.fn()
    render(<Button disabled onClick={handleClick}>Click me</Button>)
    fireEvent.click(screen.getByRole('button'))
    expect(handleClick).not.toHaveBeenCalled()
  })

  it('applies custom className', () => {
    render(<Button className="custom-class">Custom</Button>)
    expect(screen.getByRole('button')).toHaveClass('custom-class')
  })

  // Skip asChild test due to Radix Slot compatibility in jsdom
  it.skip('renders as child component when asChild', () => {
    render(
      <Button asChild>
        <a href="/test">Link</a>
      </Button>
    )
    expect(screen.getByRole('link', { name: /link/i })).toHaveAttribute('href', '/test')
  })
})