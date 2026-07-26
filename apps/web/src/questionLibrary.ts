export interface QuestionExample {
  category: string
  short: string
  prompt: string
}

export const questionCategories = [
  'All',
  'Trial Eligibility',
  'Screening Check',
  'Evidence',
  'Workflow',
  'Protocol',
  'Data Gaps',
  'External Context',
] as const

export type QuestionCategory = (typeof questionCategories)[number]

export const questionLibrary: QuestionExample[] = [
  {
    category: 'Trial Eligibility',
    short: 'Is this patient eligible for this trial?',
    prompt:
      'Is Alex Morgan eligible for the EGFR exon 20 NSCLC trial (NCT99004324), and what needs review before screening?',
  },
  {
    category: 'Screening Check',
    short: 'What is preventing this patient from moving to screening?',
    prompt:
      'What is preventing PT-1042 from moving to formal trial screening for NCT99004324?',
  },
  {
    category: 'Evidence',
    short: 'Prepare an evidence packet for PI review.',
    prompt:
      'Prepare an evidence packet for PI review of PT-1042 and NCT99004324 with patient facts, protocol criteria, and unresolved issues.',
  },
  {
    category: 'Workflow',
    short: 'Who should own the next step and what action should be drafted?',
    prompt:
      'For PT-1042 and NCT99004324, who should own the next step, and what action should be drafted for human review?',
  },
  {
    category: 'Protocol',
    short: 'Does prior therapy conflict with the trial exclusion?',
    prompt:
      'Does the prior platinum therapy history for PT-1042 conflict with the NCT99004324 exclusion criteria?',
  },
  {
    category: 'Data Gaps',
    short: 'What patient data is missing before we can advance?',
    prompt:
      'What patient data is missing or stale for PT-1042 before the NCT99004324 case can advance?',
  },
  {
    category: 'External Context',
    short: 'What external context is relevant to this biomarker?',
    prompt:
      'For Alex Morgan and NCT99004324, what external trial registry or treatment landscape context is relevant to the EGFR exon 20 biomarker?',
  },
]

export const defaultQuestion = questionLibrary[0].prompt
