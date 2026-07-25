interface EmptyStateProps {
  isLoading: boolean
}

export function EmptyState({ isLoading }: EmptyStateProps) {
  return (
    <section className="empty-state card" id="assessment">
      <div className="empty-mark" aria-hidden="true">
        {isLoading ? '...' : 'IQ'}
      </div>
      <h2>{isLoading ? 'Running assessment' : 'Ready to assess trial readiness'}</h2>
      <p>
        {isLoading
          ? 'The four IQ layers are retrieving institutional knowledge, patient data, care team workflow, and external evidence in parallel, then reasoning over them.'
          : 'Select or edit a question above, then run the assessment. The agent will query the IQ layers and prepare a cited, human reviewed trial readiness assessment.'}
      </p>
    </section>
  )
}
