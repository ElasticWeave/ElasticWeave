#!/usr/bin/env python3
import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "references" / "insight-cycle.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)


def solidated_record() -> dict:
    lemma = {
        "statement": "The intervention causes the measured change in this scope.",
        "mechanism": "The intervention changes the measured input.",
        "prediction": "The intervention outperforms the control.",
        "competingExplanation": "The observed change is measurement noise.",
        "falsifier": "The control matches or exceeds the intervention.",
        "boundaries": "This dataset and measurement procedure only.",
    }
    evidence = ["workflow-output/experiment.json"]
    experiment = {
        "experimentId": "experiment-1",
        "intervention": "Apply the intervention.",
        "control": "Run the unchanged baseline.",
        "measurement": "Compare the predeclared metric.",
        "decisionRule": "Support only when the intervention exceeds the control.",
        "validityRequirements": ["Both runs use the same inputs."],
        "stopCondition": "Stop after the predeclared comparison.",
        "result": "SUPPORTS",
        "rawEvidenceRefs": evidence,
    }
    return {
        "schema": "insight-cycle/v1",
        "cycleId": "cycle-1",
        "question": "Does the intervention cause the observed change?",
        "scope": "One bounded project decision.",
        "iterationMode": "autonomous",
        "state": "SOLIDATED",
        "budget": {
            "maxRounds": 1,
            "maxExperiments": 1,
            "roundsUsed": 1,
            "experimentsUsed": 1,
        },
        "seed": {
            "expectedObservation": "The intervention exceeds the control.",
            "actualOperation": "Ran the intervention and control.",
            "rawObservation": "The intervention exceeded the control.",
            "comparator": "The unchanged baseline.",
            "measure": "The predeclared metric.",
            "evidenceRefs": evidence,
        },
        "lemma": lemma,
        "verification": [experiment],
        "rounds": [
            {
                "round": 1,
                "lemma": lemma,
                "experimentId": "experiment-1",
                "result": "SUPPORTS",
                "discrepancy": "No material discrepancy.",
                "decision": "Solidate within the declared boundary.",
            }
        ],
        "outcome": {
            "terminal": "SOLIDATED",
            "conclusion": "The bounded evidence supports the lemma.",
            "evidenceRefs": evidence,
            "limitations": ["No claim beyond the declared scope."],
            "nextProbe": None,
            "failureClass": None,
        },
    }


class InsightCycleSchemaTests(unittest.TestCase):
    def test_solidated_record_requires_supporting_round_trace(self) -> None:
        record = solidated_record()
        self.assertEqual([], list(VALIDATOR.iter_errors(record)))

        without_rounds = copy.deepcopy(record)
        without_rounds["rounds"] = []
        self.assertTrue(list(VALIDATOR.iter_errors(without_rounds)))

        unsupported_round = copy.deepcopy(record)
        unsupported_round["rounds"][0]["result"] = "INCONCLUSIVE"
        self.assertTrue(list(VALIDATOR.iter_errors(unsupported_round)))


if __name__ == "__main__":
    unittest.main()
