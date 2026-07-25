import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Evidence } from '../types'
import { EvidencePacket } from './EvidencePacket'
import { evidenceHref, sourceTypeLabel } from './evidencePresentation'

const workEvidence: Evidence = {
  refId: 'work-1',
  source: 'work',
  title: 'Tumor board follow-up',
  snippet: 'A coordinator follow-up was assigned.',
  url: 'https://contoso.sharepoint.com/sites/care-team/follow-up',
  sourceType: 'teams_message',
}

describe('EvidencePacket links', () => {
  it('renders attributed Work IQ evidence as a clearly labeled safe link', () => {
    const markup = renderToStaticMarkup(<EvidencePacket evidence={[workEvidence]} criteria={[]} />)

    expect(markup).toContain('Tumor board follow-up')
    expect(markup).toContain('Teams message')
    expect(markup).toContain('href="https://contoso.sharepoint.com/sites/care-team/follow-up"')
    expect(markup).toContain('rel="noopener noreferrer"')
    expect(markup).toContain('Open source')
  })

  it('keeps evidence without a URL non-clickable', () => {
    const markup = renderToStaticMarkup(
      <EvidencePacket evidence={[{ ...workEvidence, url: null }]} criteria={[]} />,
    )

    expect(markup).not.toContain('<a ')
    expect(markup).toContain('Not linked')
  })

  it('rejects unsafe and unsupported evidence URLs', () => {
    expect(evidenceHref('javascript:alert(1)')).toBeUndefined()
    expect(evidenceHref('data:text/html,unsafe')).toBeUndefined()
    expect(evidenceHref('relative/document')).toBeUndefined()
    expect(evidenceHref('/api/evidence/doc?id=1')).toBe('/api/evidence/doc?id=1')
  })

  it('makes machine-readable source types presentable', () => {
    expect(sourceTypeLabel('fabric_data_agent')).toBe('Fabric data agent')
    expect(sourceTypeLabel('')).toBe('Evidence')
  })

  it('does not relabel workflow evidence as an eligibility criterion', () => {
    const markup = renderToStaticMarkup(
      <EvidencePacket
        evidence={[workEvidence]}
        criteria={[
          {
            text: 'Prior platinum requires PI review',
            status: 'uncertain',
            evidenceRefs: ['work-1'],
          },
        ]}
        intent="workflow"
      />,
    )

    expect(markup).toContain('Care team workflow context')
    expect(markup).not.toContain('Prior platinum requires PI review')
  })
})
