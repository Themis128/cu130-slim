import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { LeadCapture, LeadCaptureDone } from '@/components/marketing/LeadCapture'

const PDF = 'https://cloudless.gr/playbooks/cloud-migration-playbook.pdf'

describe('LeadCaptureDone', () => {
  afterEach(() => cleanup())

  it('only promises an email when one was queued', () => {
    render(<LeadCaptureDone result={{ playbook_delivery: 'email', playbook_url: PDF }} />)
    expect(screen.getByText(/check your inbox/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /download it now/i })).toHaveAttribute('href', PDF)
  })

  it('offers the direct download without promising an email when no sender is configured', () => {
    render(<LeadCaptureDone result={{ playbook_delivery: 'download', playbook_url: PDF }} />)
    expect(screen.queryByText(/check your inbox/i)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /download the playbook/i })).toHaveAttribute('href', PDF)
  })

  it('says we will be in touch when there is no playbook at all', () => {
    render(<LeadCaptureDone result={{ playbook_delivery: 'none', playbook_url: null }} />)
    expect(screen.getByText(/we'll be in touch/i)).toBeInTheDocument()
    expect(screen.queryByText(/check your inbox/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('does not re-promise an email to an address that already got it', () => {
    render(<LeadCaptureDone result={{ playbook_delivery: 'already_sent', playbook_url: PDF }} />)
    expect(screen.getByText(/already on the list/i)).toBeInTheDocument()
    expect(screen.queryByText(/check your inbox/i)).not.toBeInTheDocument()
  })
})

describe('LeadCapture', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders the backend delivery result after submit', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, playbook_delivery: 'download', playbook_url: PDF }),
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<LeadCapture />)
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'a@b.co' },
    })
    fireEvent.click(screen.getByRole('button', { name: /get the playbook/i }))
    await waitFor(() =>
      expect(screen.getByRole('link', { name: /download the playbook/i })).toBeInTheDocument(),
    )
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/leads/public', expect.objectContaining({ method: 'POST' }))
    expect(screen.queryByText(/check your inbox/i)).not.toBeInTheDocument()
  })

  it('shows an error when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }))
    render(<LeadCapture />)
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'a@b.co' },
    })
    fireEvent.click(screen.getByRole('button', { name: /get the playbook/i }))
    await waitFor(() => expect(screen.getByText(/something went wrong/i)).toBeInTheDocument())
  })
})
