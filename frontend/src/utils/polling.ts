export function isAbortError(caught: unknown): boolean {
  return caught instanceof Error && caught.name === 'AbortError'
}

export function waitForPoll(intervalMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(new DOMException('Polling cancelled', 'AbortError'))

  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, intervalMs)

    function onAbort(): void {
      globalThis.clearTimeout(timer)
      reject(new DOMException('Polling cancelled', 'AbortError'))
    }

    signal.addEventListener('abort', onAbort, { once: true })
  })
}
