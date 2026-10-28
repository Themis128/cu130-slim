import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { Input } from '@/components/ui/Input'

describe('Input', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders input element', () => {
    render(<Input placeholder="Enter text" />)
    expect(screen.getByPlaceholderText('Enter text')).toBeInTheDocument()
  })

  it('applies value and handles onChange', () => {
    const handleChange = vi.fn()
    render(<Input value="test" onChange={handleChange} />)
    const input = screen.getByRole('textbox')
    expect(input).toHaveValue('test')
    fireEvent.change(input, { target: { value: 'updated' } })
    expect(handleChange).toHaveBeenCalled()
  })

  it('shows error state', () => {
    render(<Input error="This field is required" />)
    expect(screen.getByRole('alert')).toHaveTextContent('This field is required')
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-invalid', 'true')
  })

  it('handles disabled state', () => {
    render(<Input disabled />)
    expect(screen.getByRole('textbox')).toBeDisabled()
  })

  it('applies custom className', () => {
    render(<Input className="custom-input" />)
    expect(screen.getByRole('textbox')).toHaveClass('custom-input')
  })

  it('forwards ref', () => {
    const ref = vi.fn()
    render(<Input ref={ref} />)
    expect(ref).toHaveBeenCalled()
  })
})