// Vitest setup for component tests: register jest-dom matchers (toBeInTheDocument, etc.)
// and unmount React trees after each test so queries never see a previous render.
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => cleanup())
