// Vitest setup for component tests: register jest-dom matchers (toBeInTheDocument, etc.)
// and unmount React trees after each test so queries never see a previous render.
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom doesn't implement scrollIntoView; the chat view auto-scrolls to the newest message.
if (!window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = () => {}
}

// jsdom doesn't implement PointerEvent, so fireEvent.pointer* would drop clientX/clientY (the
// studio uses a pointerdown→window move/up drag model). Polyfill it as a MouseEvent subclass
// (MouseEvent carries clientX/clientY/button) so pointer-drag tests can simulate real coordinates.
if (typeof window.PointerEvent === 'undefined') {
  class PointerEvent extends window.MouseEvent {
    constructor(type, params = {}) {
      super(type, params)
      this.pointerId = params.pointerId ?? 1
      this.pointerType = params.pointerType ?? 'mouse'
      this.isPrimary = params.isPrimary ?? true
    }
  }
  window.PointerEvent = PointerEvent
  globalThis.PointerEvent = PointerEvent
}

afterEach(() => cleanup())
