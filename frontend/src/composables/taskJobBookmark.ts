export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

const prefix = 'gwap.job-bookmark.v1'

function storage(): StorageLike | undefined {
  if (typeof window === 'undefined') return undefined
  try { return window.sessionStorage } catch { return undefined }
}

export function taskJobBookmarkKey(principalId: string | undefined, scope: string): string | undefined {
  const user = principalId?.trim()
  const operation = scope.trim()
  if (!user || !operation) return undefined
  return `${prefix}:${encodeURIComponent(user)}:${encodeURIComponent(operation)}`
}

export function saveTaskJobBookmark(principalId: string | undefined, scope: string, jobId: string, target = storage()): void {
  const key = taskJobBookmarkKey(principalId, scope)
  const value = jobId.trim()
  if (!key || !value || value.length > 256) return
  try { target?.setItem(key, value) } catch { /* Storage may be unavailable in private browser contexts. */ }
}

export function readTaskJobBookmark(principalId: string | undefined, scope: string, target = storage()): string | undefined {
  const key = taskJobBookmarkKey(principalId, scope)
  if (!key) return undefined
  try {
    const value = target?.getItem(key)?.trim()
    return value && value.length <= 256 ? value : undefined
  } catch { return undefined }
}

export function clearTaskJobBookmark(principalId: string | undefined, scope: string, target = storage()): void {
  const key = taskJobBookmarkKey(principalId, scope)
  if (!key) return
  try { target?.removeItem(key) } catch { /* Nothing else can be safely done. */ }
}

export function shouldClearTaskJobBookmark(error: unknown): boolean {
  const status = Number((error as any)?.response?.status ?? (error as any)?.status)
  return [401, 403, 404].includes(status)
}
