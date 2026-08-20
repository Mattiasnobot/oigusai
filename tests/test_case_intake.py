import json
import unittest
from unittest.mock import Mock, patch

from services.case_intake import CaseIntakeService


class CaseIntakeTests(unittest.TestCase):
    def test_one_word_input_gets_simple_clarification_questions(self):
        ai = Mock()
        service = CaseIntakeService(ai)

        result = service.understand("Koondamine")

        self.assertEqual(result["input_type"], "fragment")
        self.assertFalse(result["ready_for_analysis"])
        self.assertEqual(len(result["clarification_questions"]), 3)
        self.assertIn("Koondamine", result["summary"])
        ai.generate_structured.assert_not_called()

    def test_fragment_summary_removes_space_before_terminal_punctuation(self):
        service = CaseIntakeService(Mock())

        result = service.understand("Koondamine .")

        self.assertEqual(result["topic"], "Koondamine")
        self.assertEqual(result["summary"], "Kirjutasid teemaks „Koondamine“.")

    def test_model_questions_are_polished_before_display(self):
        questions = CaseIntakeService._questions([
            "Kas töötaja on teadlik, millist konkreetset vigu teda süüdistatakse?",
            "Kui kaua kestis töösuhkus enne seda sündmust?",
        ])

        self.assertEqual(questions, [
            "Kas tööandja ütles, millises konkreetses rikkumises ta sind süüdistab?",
            "Kui kaua olid enne vallandamist selle tööandja juures töötanud?",
        ])

    def test_structured_intake_keeps_only_items_with_user_text_evidence(self):
        original = "Üürileandja küsib minult nelja kuu tagatisraha. Soovin teada, kas see on lubatud."
        payload = {
            "input_type": "question",
            "topic": "eluruumi tagatisraha",
            "summary": "Üürileandja küsib nelja kuu tagatisraha.",
            "user_goal": "Soovib teada, kas nõue on lubatud.",
            "help_types": ["rights_explanation"],
            "parties": [
                {"role": "üürileandja", "label": "teine osapool", "evidence": "Üürileandja"},
                {"role": "maakler", "label": "kolmas osapool", "evidence": "maakler"},
            ],
            "events": [{
                "date": "teadmata",
                "actor": "üürileandja",
                "action": "küsib nelja kuu tagatisraha",
                "evidence": "Üürileandja küsib minult nelja kuu tagatisraha",
            }],
            "amounts": [{
                "label": "tagatisraha",
                "value": "neli kuud",
                "evidence": "nelja kuu tagatisraha",
            }],
            "documents": [],
            "missing_facts": ["kas tegemist on eluruumiga"],
            "clarification_questions": [],
            "ready_for_analysis": True,
            "search_query": "eluruumi üürilepingu nelja kuu tagatisraha",
        }
        ai = Mock()
        ai.generate_structured.return_value = json.dumps(payload, ensure_ascii=False)
        service = CaseIntakeService(ai)

        result = service.understand(original)

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(len(result["parties"]), 1)
        self.assertEqual(result["parties"][0]["role"], "üürileandja")
        self.assertEqual(result["amounts"][0]["value"], "neli kuud")
        self.assertIn("Kasutaja soovitud abi", result["analysis_context"])
        self.assertEqual(result["search_query"], original)

    def test_summary_with_invented_number_falls_back_to_original_text(self):
        original = "Tööandja teatas koondamisest."
        payload = {
            "input_type": "narrative",
            "topic": "koondamine",
            "summary": "Tööandja teatas, et töösuhe lõpeb kolme päeva pärast.",
            "user_goal": "Soovib õiguste selgitust.",
            "help_types": ["rights_explanation"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": [],
            "clarification_questions": [],
            "ready_for_analysis": True,
            "search_query": "koondamine kolme päeva pärast",
        }
        ai = Mock()
        ai.generate_structured.return_value = json.dumps(payload, ensure_ascii=False)
        service = CaseIntakeService(ai)

        result = service.understand(original)

        self.assertIn(original, result["summary"])
        self.assertNotIn("kolme päeva", result["summary"])
        self.assertEqual(result["search_query"], original)

    def test_clear_free_form_request_uses_deterministic_facts_without_extra_questions(self):
        original = (
            "Üürileandja küsib nelja kuu tagatisraha. Lepingut pole allkirjastatud "
            "ja soovin teada, mida peaksin tegema."
        )
        payload = {
            "input_type": "narrative",
            "topic": "tagatisraha",
            "summary": "Üürileandja esitas enneaegse õigusvastase nõude.",
            "user_goal": "Soovib teada, mida teha.",
            "help_types": ["next_steps"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": ["eluruumi liik"],
            "clarification_questions": ["Kas nõue on juba tasutud?"],
            "ready_for_analysis": False,
            "search_query": "tagatisraha nõue",
        }
        ai = Mock()
        ai.generate_structured.return_value = json.dumps(payload, ensure_ascii=False)
        service = CaseIntakeService(ai)

        result = service.understand(original)

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(result["clarification_questions"], [])
        self.assertEqual(result["parties"][0]["role"], "üürileandja")
        self.assertIn("nelja kuu", result["amounts"][0]["value"])
        self.assertIn("Üürileandja küsib", result["summary"])
        self.assertNotIn("õigusvastase", result["summary"])

    def test_model_failure_still_allows_normal_question_to_continue(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)

        result = service.understand(
            "Tööandja teatas koondamisest ja ma soovin teada oma õigusi."
        )

        self.assertTrue(result["ready_for_analysis"])
        self.assertFalse(result["used_ai"])
        self.assertEqual(result["clarification_questions"], [])
        ai.generate_structured.assert_called_once()

    def test_direct_question_skips_extra_intake_model_call(self):
        ai = Mock()
        service = CaseIntakeService(ai)

        result = service.understand("Kas tööleping peab olema kirjalik?")

        self.assertTrue(result["ready_for_analysis"])
        self.assertFalse(result["used_ai"])
        self.assertEqual(result["search_query"], "Kas tööleping peab olema kirjalik?")
        ai.generate_structured.assert_not_called()

    def test_long_narrative_is_split_and_merged_before_legal_search(self):
        ai = Mock()
        service = CaseIntakeService(ai)
        long_text = ("Tööandja teatas kirjalikult, et töökoht kaob. " * 420).strip()
        partial = service._fallback_result(
            "Tööandja teatas kirjalikult, et töökoht kaob."
        )

        with patch.object(service, "_understand_chunk", return_value=partial) as understand:
            result = service.understand(long_text)

        self.assertGreater(understand.call_count, 1)
        self.assertEqual(result["input_length"], len(long_text))
        self.assertIn("Tööandja", result["analysis_context"])
        self.assertLessEqual(len(result["search_query"]), 2000)

    def test_fine_event_gets_one_decisive_follow_up_after_generic_answers(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)
        text = (
            "Abipolitsei\n\nKasutaja täpsustused:\n"
            "ÕigusAI küsimus: Mis täpselt juhtus?\n"
            "Kasutaja vastus: Trahvis mind ilma asjata.\n"
            "ÕigusAI küsimus: Kes on osapooled?\n"
            "Kasutaja vastus: Mina ja abipolitsei.\n"
            "ÕigusAI küsimus: Millist abi vajad?\n"
            "Kasutaja vastus: Õiguste selgitust ja järgmisi samme."
        )

        result = service.understand(text)

        self.assertFalse(result["ready_for_analysis"])
        self.assertEqual(len(result["clarification_questions"]), 1)
        self.assertIn("trahviotsuse", result["clarification_questions"][0])
        self.assertNotIn("Mis täpselt juhtus", result["search_query"])
        self.assertNotIn("Kasutaja täpsustused", result["search_query"])

    def test_answered_fine_follow_up_is_not_repeated(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)
        text = (
            "Abipolitsei. Trahvis mind ilma asjata.\n"
            "ÕigusAI küsimus: Kas said trahviotsuse või trahviteate, mis rikkumine "
            "sinna märgiti ja millal said selle kätte?\n"
            "Kasutaja vastus: Ma ei tea, dokumenti mulle ei antud."
        )

        result = service.understand(text)

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(result["clarification_questions"], [])

    def test_received_fine_notice_asks_for_decision_maker_and_exact_section(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)
        text = (
            "Abipolitsei. Trahvis mind ilma asjata.\n"
            "ÕigusAI küsimus: Kas said trahviotsuse või trahviteate, mis rikkumine "
            "sinna märgiti ja millal said selle kätte?\n"
            "Kasutaja vastus: Sain trahviteate. Politsei töö segamine. 10.08.2026."
        )

        result = service.understand(text)

        self.assertFalse(result["ready_for_analysis"])
        self.assertEqual(len(result["clarification_questions"]), 1)
        self.assertIn("kohtuvälise menetleja", result["clarification_questions"][0])
        self.assertIn("paragrahv", result["clarification_questions"][0])

    def test_answered_decision_maker_follow_up_continues_to_analysis(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)
        text = (
            "Abipolitsei. Trahvis mind ilma asjata. Sain trahviteate.\n"
            "ÕigusAI küsimus: Kas said trahviotsuse või trahviteate, mis rikkumine "
            "sinna märgiti ja millal said selle kätte?\n"
            "Kasutaja vastus: Sain trahviteate 10.08.2026.\n"
            "ÕigusAI küsimus: Kes on trahviteatel „kohtuvälise menetleja” ja „otsuse "
            "teinud ametnikuna” kirjas ning milline seaduse paragrahv on rikkumise "
            "juures märgitud?\n"
            "Kasutaja vastus: PPA, ametnik Kask, KarS § 262."
        )

        result = service.understand(text)

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(result["clarification_questions"], [])

    def test_personal_identifier_question_is_blocked_and_analysis_continues(self):
        ai = Mock()
        ai.generate_structured.return_value = json.dumps({
            "input_type": "narrative",
            "topic": "ametniku teade",
            "summary": "Ametnik teatas otsusest.",
            "user_goal": "Soovib olukorra selgitust.",
            "help_types": ["rights_explanation"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": ["kasutaja isik"],
            "clarification_questions": [
                "Milline on kasutaja täpne nimi või isikukood?"
            ],
            "ready_for_analysis": False,
            "search_query": "ametniku teade",
        }, ensure_ascii=False)
        service = CaseIntakeService(ai)

        result = service.understand("Ametnik teatas, et minu taotlus jäeti rahuldamata.")

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(result["clarification_questions"], [])

    def test_rephrased_answered_question_is_not_asked_twice(self):
        ai = Mock()
        ai.generate_structured.return_value = json.dumps({
            "input_type": "narrative",
            "topic": "ametniku otsus",
            "summary": "Ametnik teatas otsusest.",
            "user_goal": "Soovib olukorra selgitust.",
            "help_types": ["rights_explanation"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": [],
            "clarification_questions": [
                "Kas said kirjaliku teate või ainult suulise teavituse?"
            ],
            "ready_for_analysis": False,
            "search_query": "ametniku otsus",
        }, ensure_ascii=False)
        service = CaseIntakeService(ai)
        text = (
            "Ametnik teatas, et minu taotlus jäeti rahuldamata.\n"
            "ÕigusAI küsimus: Kas said kirjaliku otsuse või ainult suulise teavituse?\n"
            "Kasutaja vastus: Sain mõlemad, nii kirjaliku kui ka suulise teavituse."
        )

        result = service.understand(text)

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(result["clarification_questions"], [])

    def test_flexible_auxiliary_police_fine_wording_asks_for_document(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)

        result = service.understand(
            "Abipolitsei tegi mulle niisama seismise eest trahvi."
        )

        self.assertFalse(result["ready_for_analysis"])
        self.assertEqual(len(result["clarification_questions"]), 1)
        self.assertIn("trahviotsuse", result["clarification_questions"][0])

    def test_challenge_language_is_kept_as_requested_help(self):
        ai = Mock()
        service = CaseIntakeService(ai)

        result = service.understand(
            "Kas oleks võimalik seda trahvi kuidagi vaidlustada?"
        )

        self.assertIn("challenge_decision", result["help_types"])
        self.assertIn("vaidlustamise", result["user_goal"])

    def test_latest_turn_plans_missed_deadline_and_payment_before_search(self):
        ai = Mock()
        ai.generate_structured.side_effect = RuntimeError("model unavailable")
        service = CaseIntakeService(ai)
        current = (
            "Kui kaebe tähtaeg on möödas, kas saan seda ikka vaidlustada? "
            "Soovin 4000 eurot maksta järelmaksuga."
        )
        conversation = (
            "Abipolitsei tegi mulle trahvi. Sain trahviteate.\n"
            "ÕigusAI küsimus: Kas soovid midagi lisada või parandada?\n"
            f"Kasutaja vastus: {current}"
        )

        result = service.understand(conversation, current_message=current)

        self.assertFalse(result["ready_for_analysis"])
        self.assertEqual(result["next_action"], "clarify")
        self.assertIn("missed_deadline", result["current_intents"])
        self.assertIn("challenge_decision", result["current_intents"])
        self.assertIn("payment_plan", result["current_intents"])
        self.assertEqual(len(result["clarification_questions"]), 1)
        self.assertIn("dokumendi täpne pealkiri", result["clarification_questions"][0])
        self.assertIn("kohtutäituri", result["clarification_questions"][0])
        self.assertIn("4000 eurot", result["clarification_questions"][0])
        self.assertIn("tähtaja ennistamise", result["search_query"])
        self.assertIn("tasumine ositi", result["search_query"])
        self.assertIn("möödunud kaebetähtajast", result["turn_summary"])

    def test_latest_turn_answer_to_planner_continues_without_repeating(self):
        ai = Mock()
        ai.generate_structured.return_value = json.dumps({
            "input_type": "narrative",
            "topic": "rahatrahvi vaidlustamine",
            "summary": "Kasutaja soovib trahvi vaidlustada ja ositi tasuda.",
            "user_goal": "Soovib teada vaidlustamise ja tasumise võimalusi.",
            "help_types": ["rights_explanation"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": ["otsuse teinud ametnik"],
            "clarification_questions": [
                "Kes on otsuse teinud ametnikuna kirjas?"
            ],
            "ready_for_analysis": False,
            "search_query": "rahatrahvi vaidlustamine",
        }, ensure_ascii=False)
        service = CaseIntakeService(ai)
        question = (
            "Palun vaata dokumendilt, mis on dokumendi täpne pealkiri, millal "
            "said selle kätte, kas see on juba kohtutäituri või muu täitja käes?"
        )
        conversation = (
            "Sain rahatrahvi. Kaebe tähtaeg on möödas ja soovin maksta osade kaupa.\n"
            f"ÕigusAI küsimus: {question}\n"
            "Kasutaja vastus: Ma ei tea."
        )

        result = service.understand(conversation, current_message="Ma ei tea.")

        self.assertTrue(result["ready_for_analysis"])
        self.assertEqual(result["next_action"], "analyze")
        self.assertEqual(result["clarification_questions"], [])
        self.assertIn("missed_deadline", result["current_intents"])
        self.assertIn("payment_plan", result["current_intents"])


if __name__ == "__main__":
    unittest.main()
