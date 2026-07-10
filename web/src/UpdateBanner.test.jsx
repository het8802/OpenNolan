// The banner is hidden with no Electron bridge, appears when an update is pushed/staged, and
// "Restart & update" invokes the install channel.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, act, waitFor } from '@testing-library/react'
import UpdateBanner from './UpdateBanner.jsx'

afterEach(() => { delete window.openNolan })

// Wire a fake bridge; capture the onDownloaded callback so a test can push an update.
function fakeBridge({ staged = null } = {}) {
  let pushed
  window.openNolan = {
    update: {
      getState: vi.fn(() => Promise.resolve(staged)),
      install: vi.fn(() => Promise.resolve(true)),
      onDownloaded: vi.fn((cb) => { pushed = cb; return () => {} }),
    },
  }
  return { push: (info) => act(() => pushed(info)) }
}

describe('UpdateBanner', () => {
  it('renders nothing without an Electron bridge', () => {
    const { container } = render(<UpdateBanner />)
    expect(container.firstChild).toBeNull()
  })

  it('shows on a live push and installs on click', async () => {
    const { push } = fakeBridge()
    const { queryByText, getByText } = render(<UpdateBanner />)
    expect(queryByText('Update ready')).toBeNull()

    push({ version: '1.2.3' })
    expect(getByText('Update ready')).toBeTruthy()
    expect(getByText(/Version 1\.2\.3/)).toBeTruthy()

    fireEvent.click(getByText('Restart & update'))
    expect(window.openNolan.update.install).toHaveBeenCalled()
  })

  it('re-hydrates a staged update after reload', async () => {
    fakeBridge({ staged: { version: '9.9.9' } })
    const { findByText } = render(<UpdateBanner />)
    expect(await findByText(/Version 9\.9\.9/)).toBeTruthy()
  })
})
