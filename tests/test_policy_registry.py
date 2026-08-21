import unittest

from services.policy_registry import CoveragePolicyRegistry


class CoveragePolicyRegistryTests(unittest.TestCase):
    def test_snapshot_exposes_versioned_audited_rules(self):
        snapshot = CoveragePolicyRegistry.snapshot()

        self.assertEqual(snapshot["version"], "V10.4-coverage-policy-1")
        self.assertEqual(snapshot["rule_count"], 6)
        self.assertEqual(
            [row["reason"] for row in snapshot["rules"]],
            [
                "employment_context:redundancy_basis",
                "employment_context:notice_period",
                "employment_context:termination_form",
                "fine_context:missed_deadline",
                "fine_context:challenge_decision",
                "fine_context:payment_plan",
            ],
        )
        self.assertEqual(
            len({row["rule_id"] for row in snapshot["rules"]}),
            snapshot["rule_count"],
        )

    def test_registry_keeps_v10_3_source_contracts(self):
        expected = {
            "employment_context:redundancy_basis": (("TLS_89",),),
            "employment_context:notice_period": (("TLS_97",),),
            "employment_context:termination_form": (("TLS_95",),),
            "fine_context:missed_deadline": (("VTMS_118",),),
            "fine_context:challenge_decision": (("VTMS_114", "VTMS_118"),),
            "fine_context:payment_plan": (("KARS_66",),),
        }

        for reason, source_groups in expected.items():
            with self.subTest(reason=reason):
                rule = CoveragePolicyRegistry.get(reason)
                self.assertIsNotNone(rule)
                self.assertEqual(rule.source_groups, source_groups)

        form_rule = CoveragePolicyRegistry.get(
            "employment_context:termination_form"
        )
        self.assertEqual(
            form_rule.required_answer_terms,
            (
                ("kirjalikku taasesitamist võimaldavas vormis",),
                ("tühine",),
            ),
        )
        challenge_rule = CoveragePolicyRegistry.get(
            "fine_context:challenge_decision"
        )
        self.assertEqual(
            challenge_rule.required_answer_terms,
            (("kaebus", "vaidlust", "maakoht"),),
        )

    def test_unknown_reason_remains_unresolved(self):
        self.assertIsNone(CoveragePolicyRegistry.get("unknown:reason"))

    def test_registry_mapping_is_immutable(self):
        with self.assertRaises(TypeError):
            CoveragePolicyRegistry._RULES["test:rule"] = object()


if __name__ == "__main__":
    unittest.main()
