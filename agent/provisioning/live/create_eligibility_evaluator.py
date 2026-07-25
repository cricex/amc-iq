"""Create the AMC IQ Foundry eligibility-evaluator agent and test its consistency.

Architecture: the Fabric Data Agent retrieves the criteria + patient facts; this Foundry agent is the
eligibility EXPERT that reasons over the provided facts with explicit rules and emits a strict,
machine-readable verdict. It has NO tools (it never retrieves data itself), so it is far more
consistent than combined NL2SQL + reasoning.
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from live_environment import required_environment

PROJECT_ENDPOINT = required_environment("PROJECT_ENDPOINT")
AGENT_NAME = required_environment("EVALUATOR_AGENT_NAME")
MODEL = required_environment("AGENT_MODEL")

INSTRUCTIONS = """You are the AMC IQ clinical-trial ELIGIBILITY EVALUATOR. The user message gives you a
single trial's criteria and one patient's facts, already retrieved from the data platform. Evaluate
EACH provided criterion strictly by the rules below and output ONLY the machine-readable block. You do
not retrieve data; reason only over the facts in the message. Never invent criteria or facts.

Each criterion is: criterion_id, kind (inclusion or exclusion), category, description, and a rule
(references_entity, param, comparator, value).

Rules by category:
- diagnosis (param cancer_type): met if the patient's cancer_type matches value; value "Solid tumor"
  matches any cancer type. Otherwise not_met.
- performance (param ecog_ps): met if the patient's ECOG is <= value; otherwise not_met.
- biomarker: met if the patient has the required marker (value) with a positive status (Detected,
  Positive, Amplified, High, or MSI-High). If the patient has NO biomarker data at all, uncertain.
  Otherwise not_met.
- renal (param CrCl_CKD-EPI): met if the latest CrCl >= value. If the latest CrCl is at most 5 below
  value, OR a prior CrCl was >= value, then uncertain (borderline: repeat draw and PI confirmation).
  Otherwise not_met.
- prior_therapy as an EXCLUSION (param drug_class): if the patient received a therapy of that
  drug_class, then not_met, UNLESS an amendment modifies this criterion_id, in which case uncertain
  (PI confirmation). If the patient had no such therapy, met (the patient passes the exclusion).
  As an INCLUSION: met if the therapy is present, otherwise not_met.
- any other category: met if clearly satisfied, uncertain if the needed data is missing or ambiguous,
  not_met if clearly failed.

For an EXCLUSION criterion, "met" means the patient PASSES (is not excluded).

Output ONLY, one line per provided criterion, in the given order:
CRIT|<criterion_id>|<category>|<met|uncertain|not_met>|<short reason grounded in the provided facts>
then exactly one final line:
OVERALL|<not_eligible|likely_eligible_pending|eligible>
where OVERALL is not_eligible if any inclusion is not_met or any exclusion is not_met; else
likely_eligible_pending if any criterion is uncertain; else eligible.
Output no other text. This is a sandbox; do not give medical advice."""

# Realistic facts for PT-1042 / NCT99004324 (as the Fabric Data Agent would return them).
FACTS = """Trial NCT99004324 criteria:
- NCT99004324-DX | inclusion | diagnosis | Histologically confirmed metastatic NSCLC | references_entity=Patient param=cancer_type comparator=matches value=NSCLC
- NCT99004324-BIO | inclusion | biomarker | Documented EGFR exon 20 insertion | references_entity=Biomarker param=marker comparator=present value=EGFR exon 20 insertion
- NCT99004324-PS | inclusion | performance | ECOG performance status 0 to 1 | references_entity=Patient param=ecog_ps comparator=<= value=1
- NCT99004324-REN | inclusion | renal | CrCl >= 50 mL/min (CKD-EPI) | references_entity=Lab param=CrCl_CKD-EPI comparator=>= value=50
- NCT99004324-RX | exclusion | prior_therapy | Prior platinum doublet chemotherapy | references_entity=Treatment param=drug_class comparator=excludes value=Platinum doublet

Patient PT-1042 facts:
- cancer_type: NSCLC (metastatic adenocarcinoma)
- ecog_ps: 1
- biomarkers: EGFR exon 20 insertion = Detected
- CrCl_CKD-EPI: latest 48 mL/min on 2026-06-18; prior 55 mL/min on 2026-05-20
- treatment_history: Carboplatin + Pemetrexed, drug_class=Platinum doublet, line 1
- amendments: Amendment 2 modifies criterion NCT99004324-RX (prior platinum not automatically disqualifying; PI confirmation)"""


def main() -> None:
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(model=MODEL, instructions=INSTRUCTIONS, tools=[]),
    )
    print(f"evaluator '{AGENT_NAME}' -> version {getattr(agent, 'version', '?')}, no tools")

    if not os.getenv("TEST_EVALUATOR"):
        return
    oc = client.get_openai_client()
    print("\nConsistency test (same facts, 3 runs):")
    for i in range(3):
        r = oc.responses.create(
            input=f"Evaluate eligibility.\n\n{FACTS}",
            extra_body={"agent_reference": {"name": AGENT_NAME, "type": "agent_reference"}},
        )
        text = (getattr(r, "output_text", None) or str(r)).strip()
        print(f"--- run {i + 1} ---")
        for line in text.splitlines():
            if line.startswith("CRIT|") or line.startswith("OVERALL|"):
                print(" ", line)


if __name__ == "__main__":
    main()
