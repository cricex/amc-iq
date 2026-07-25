import type { Criterion, Patient } from '../types'

interface PatientSnapshotProps {
  patient: Patient
  criteria: Criterion[]
}

function renalThreshold(criteria: Criterion[]): number {
  const renal = criteria.find((item) => /crcl/i.test(item.text))
  const match = renal?.text.match(/(\d+)/)
  return match ? Number(match[1]) : 50
}

export function PatientSnapshot({ patient, criteria }: PatientSnapshotProps) {
  const threshold = renalThreshold(criteria)
  const crclBelow = patient.crcl < threshold

  const rows: Array<[string, React.ReactNode]> = [
    ['Patient', `${patient.display} (${patient.id})`],
    ['MRN', patient.mrn],
    ['Diagnosis', patient.diagnosis],
    ['Biomarker', patient.biomarkers.join(', ')],
    ['Stage', patient.stage],
    ['ECOG performance status', <span key="ecog" className="ecog-badge">{patient.ecog}</span>],
    [
      `CrCl (${patient.crclDate})`,
      <span key="crcl" className={crclBelow ? 'crcl-alert' : ''}>
        {patient.crcl} mL/min{crclBelow ? ` (below ${threshold} threshold)` : ''}
      </span>,
    ],
  ]

  return (
    <section className="patient-card card" id="patient">
      <div className="card-header">
        <span className="eyebrow">Patient snapshot</span>
      </div>
      <dl className="patient-grid">
        {rows.map(([label, value]) => (
          <div className="patient-row" key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
