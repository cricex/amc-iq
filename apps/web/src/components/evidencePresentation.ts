import { resolveApiUrl } from '../api/client'

export function evidenceHref(url: string | null): string | undefined {
  const candidate = url?.trim()
  if (!candidate) return undefined
  if (candidate.startsWith('/api/')) return resolveApiUrl(candidate)

  try {
    const parsed = new URL(candidate)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : undefined
  } catch {
    return undefined
  }
}

export function sourceTypeLabel(sourceType: string): string {
  const label = sourceType.trim().replace(/[_-]+/g, ' ')
  return label ? `${label.charAt(0).toUpperCase()}${label.slice(1)}` : 'Evidence'
}
