import unittest

from verifiers.relevance_verifier import RelevanceVerifier


class RelevanceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = RelevanceVerifier()
        self.query = "Abipolitsei trahvis mind ilma asjata."

    def test_rejects_real_but_off_topic_sections(self):
        laws = [{
            "id": "ABIPOLS_42",
            "title": "Abipolitseiniku seadus § 42",
            "text": "Abipolitseiniku staatusest vabastamine.",
            "domain": "ABIPOLS",
        }]

        result = self.verifier.verify_laws("Sain trahvi ilma asjata.", laws)

        self.assertFalse(result.relevant)
        self.assertEqual(result.missing_concepts, ("fine",))
        self.assertIn("trahvi", result.clarification)

    def test_accepts_sources_that_cover_the_action(self):
        laws = [{
            "id": "VTMS_57",
            "title": "Väärteomenetluse seadustik § 57",
            "text": "Kiirmenetluse otsuses märgitakse rahatrahvi määr.",
            "domain": "VTMS",
        }]

        result = self.verifier.verify_laws("Sain trahvi ilma asjata.", laws)

        self.assertTrue(result.relevant)

    def test_flexible_fine_wording_detects_fine_and_challenge(self):
        concepts = self.verifier.detect_concepts(
            "Abipolitsei tegi mulle niisama seismise eest trahvi. "
            "Kas seda saab vaidlustada?"
        )

        self.assertIn("fine", concepts)
        self.assertIn("fine_challenge", concepts)

    def test_challenge_answer_must_address_challenge_route(self):
        laws = [
            {
                "id": "ABIPOLS_3",
                "title": "Abipolitseiniku pädevus",
                "text": "Abipolitseinik abistab politseid avaliku korra tagamisel.",
            },
            {
                "id": "VTMS_19",
                "title": "Menetlusaluse isiku õigused",
                "text": (
                    "Väärteomenetluses on menetlusalusel isikul õigus "
                    "lahend vaidlustada."
                ),
            },
        ]

        result = self.verifier.verify_answer(
            "Abipolitsei tegi mulle seismise eest trahvi. Kas saan vaidlustada?",
            "Abipolitseinik võib politseid avaliku korra tagamisel abistada.",
            laws,
        )

        self.assertFalse(result.relevant)
        self.assertIn("fine_challenge", result.missing_concepts)

    def test_missed_deadline_and_payment_are_both_required(self):
        laws = [
            {
                "id": "VTMS_118",
                "title": "Kaebuse läbi vaatamata jätmine",
                "text": "Tähtaja möödumisel tuleb esitada tähtaja ennistamise taotlus.",
                "domain": "VTMS",
            },
            {
                "id": "KARS_66",
                "title": "Karistuse kandmine ositi",
                "text": "Rahatrahvi võib mõjuvatel põhjustel määrata tasuda ositi.",
                "domain": "KARS",
            },
        ]
        query = (
            "Rahatrahvi kaebe tähtaeg on möödas. Kas saan veel kaevata ja "
            "maksta osade kaupa?"
        )

        result = self.verifier.verify_answer(
            query,
            "Tähtaja ennistamist võib taotleda [VTMS_118].",
            laws,
        )

        self.assertFalse(result.relevant)
        self.assertIn("payment_plan", result.missing_concepts)

    def test_answer_covering_missed_deadline_and_payment_is_accepted(self):
        laws = [
            {
                "id": "VTMS_118",
                "title": "Kaebuse läbi vaatamata jätmine",
                "text": "Tähtaja möödumisel tuleb esitada tähtaja ennistamise taotlus.",
                "domain": "VTMS",
            },
            {
                "id": "KARS_66",
                "title": "Karistuse kandmine ositi",
                "text": "Rahatrahvi võib mõjuvatel põhjustel määrata tasuda ositi.",
                "domain": "KARS",
            },
        ]
        query = (
            "Rahatrahvi kaebe tähtaeg on möödas. Kas saan veel kaevata ja "
            "maksta osade kaupa?"
        )
        answer = (
            "Kaebuse tähtaja ennistamist võib taotleda [VTMS_118]. "
            "Rahatrahvi võib määrata tasuda ositi [KARS_66]."
        )

        result = self.verifier.verify_answer(query, answer, laws)

        self.assertTrue(result.relevant)

    def test_answer_must_use_an_action_relevant_citation(self):
        cited_laws = [{
            "id": "ABIPOLS_3",
            "title": "Abipolitseiniku seadus § 3",
            "text": "Abipolitseiniku pädevuses on politsei abistamine.",
            "domain": "ABIPOLS",
        }]

        result = self.verifier.verify_answer(
            self.query,
            "Abipolitseiniku pädevuses on politsei abistamine [ABIPOLS_3].",
            cited_laws,
        )

        self.assertFalse(result.relevant)

    def test_auxiliary_police_fine_answer_must_cover_actor_and_procedure(self):
        laws = [{
            "id": "VTMS_114",
            "title": "Väärteomenetluse seadustik § 114",
            "text": "Kiirmenetluse otsuse peale võib esitada maakohtule kaebuse.",
            "domain": "VTMS",
        }]

        result = self.verifier.verify_answer(
            "Abipolitsei trahvis mind ja sain trahviteate.",
            "Otsuse peale võib esitada maakohtule kaebuse [VTMS_114].",
            laws,
        )

        self.assertFalse(result.relevant)
        self.assertIn("auxiliary_police", result.missing_concepts)

    def test_formal_notice_deadline_must_be_conditional_when_document_is_unclear(self):
        laws = [
            {
                "id": "ABIPOLS_3",
                "title": "Abipolitseiniku pädevus",
                "text": "Abipolitseiniku pädevuses on politsei abistamine.",
                "domain": "ABIPOLS",
            },
            {
                "id": "VTMS_54B5",
                "title": "Trahviteate vaidlustamine",
                "text": "Hoiatustrahvi trahviteate võib vaidlustada.",
                "domain": "VTMS",
            },
        ]

        result = self.verifier.verify_answer(
            "Abipolitsei trahvis mind ja sain trahviteate.",
            (
                "Abipolitseinik abistab politseid [ABIPOLS_3]. "
                "Trahviteate võib vaidlustada 30 päeva jooksul [VTMS_54B5]."
            ),
            laws,
        )

        self.assertFalse(result.relevant)
        self.assertEqual(result.missing_concepts, ("formal_notice_scope",))

    def test_conditional_formal_notice_answer_is_accepted(self):
        laws = [
            {
                "id": "ABIPOLS_3",
                "title": "Abipolitseiniku pädevus",
                "text": "Abipolitseiniku pädevuses on politsei abistamine.",
                "domain": "ABIPOLS",
            },
            {
                "id": "VTMS_54B5",
                "title": "Trahviteate vaidlustamine",
                "text": "Hoiatustrahvi trahviteate võib vaidlustada.",
                "domain": "VTMS",
            },
        ]

        result = self.verifier.verify_answer(
            "Abipolitsei trahvis mind ja sain trahviteate.",
            (
                "Abipolitseinik abistab politseid [ABIPOLS_3]. "
                "Kui saadud dokument on hoiatustrahvi trahviteade, võib selle "
                "vaidlustada 30 päeva jooksul [VTMS_54B5]."
            ),
            laws,
        )

        self.assertTrue(result.relevant)


if __name__ == "__main__":
    unittest.main()
