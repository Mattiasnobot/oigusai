import json
import unittest
from unittest.mock import Mock, patch

from config import load_settings

from services.offline_ai import (
    AI_DOCUMENT_RESPONSE_SCHEMA,
    AI_RESPONSE_SCHEMA,
    OfflineAIService,
)
from verifiers.source_verifier import SourceVerifier


class OfflineAITests(unittest.TestCase):
    def test_document_comparison_keeps_only_exact_law_and_document_inputs(self):
        service = OfflineAIService(allow_mock=False)
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Üürnik võib maksta tagatisraha kolme kuu jooksul.",
            "source": "Riigi Teataja",
        }]
        spans = [{
            "span_id": "DOC-LEASE-P1-S1",
            "document_id": "DOC-LEASE",
            "file_name": "leping.txt",
            "page": 1,
            "start": 20,
            "end": 66,
            "text": "Lepingus on tagatisraha suurus neli kuud.",
            "method": "text",
        }]
        raw = json.dumps({
            "claims": [{
                "text": "Üürnik võib maksta tagatisraha kolme kuu jooksul.",
                "source_id": "VOS_308",
                "evidence": "Üürnik võib maksta tagatisraha kolme kuu jooksul.",
            }],
            "comparisons": [{
                "text": (
                    "Dokumendis on tagatisraha neli kuud, seadus lubab "
                    "tagatisraha maksta kolme kuu jooksul."
                ),
                "law_source_id": "VOS_308",
                "law_evidence": "Üürnik võib maksta tagatisraha kolme kuu jooksul.",
                "document_span_id": "DOC-LEASE-P1-S1",
                "document_evidence": "tagatisraha suurus neli kuud",
            }],
        }, ensure_ascii=False)
        with patch.object(service, "_call_ollama", return_value=raw) as call:
            analysis, is_mock, claims = service.analyze_case_structured(
                "Kas leping vastab seadusele?",
                laws,
                document_spans=spans,
            )

        self.assertFalse(is_mock)
        self.assertIn("[VOS_308]", analysis)
        comparison = next(claim for claim in claims if claim["kind"] == "inference")
        self.assertEqual(comparison["verification_status"], "INPUTS_VERIFIED")
        self.assertEqual(
            {source["kind"] for source in comparison["sources"]},
            {"law", "document"},
        )
        self.assertEqual(
            call.call_args.kwargs["response_schema"],
            AI_DOCUMENT_RESPONSE_SCHEMA,
        )

    def test_structured_analysis_preserves_exact_verified_evidence(self):
        service = OfflineAIService(allow_mock=True)
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Üürnik võib maksta tagatisraha osadena.",
            "source": "Riigi Teataja",
        }]
        raw = json.dumps({
            "claims": [{
                "text": "Üürnik võib maksta tagatisraha osadena.",
                "source_id": "VOS_308",
                "evidence": "Üürnik võib maksta tagatisraha osadena.",
            }],
        }, ensure_ascii=False)
        with patch.object(service, "_call_ollama", return_value=raw):
            analysis, is_mock, claims = service.analyze_case_structured(
                "Kuidas tagatisraha maksta?",
                laws,
            )

        self.assertFalse(is_mock)
        self.assertIn("[VOS_308]", analysis)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["verification_status"], "EVIDENCE_VERIFIED")
        self.assertEqual(
            claims[0]["sources"][0]["evidence"],
            "Üürnik võib maksta tagatisraha osadena.",
        )
    def test_auxiliary_police_fine_prompt_requires_actor_and_procedure_coverage(self):
        laws = [
            {
                "id": "ABIPOLS_3",
                "title": "Abipolitseiniku seadus § 3",
                "text": "Abipolitseiniku pädevuses on politsei abistamine.",
                "source": "Riigi Teataja",
                "domain": "ABIPOLS",
            },
            {
                "id": "VTMS_114",
                "title": "Väärteomenetluse seadustik § 114",
                "text": "Menetlusosalisel on õigus esitada otsuse peale kaebus.",
                "source": "Riigi Teataja",
                "domain": "VTMS",
            },
        ]
        ai = OfflineAIService(allow_mock=True)

        prompt = ai._build_prompt(
            "Abipolitsei trahvis mind ja sain trahviteate.", laws
        )

        self.assertIn("vähemalt üks ABIPOLS", prompt)
        self.assertIn("vähemalt üks VTMS", prompt)
        self.assertIn("ära nimeta trahvi sisuliselt põhjendatuks", prompt)

    def test_mock_response_passes_citation_verification(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Tähtajatu üürilepingu ülesütlemise tähtaeg.",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=True)
        text = ai._mock_analysis("test", laws)
        valid, sources = SourceVerifier().verify_sources(text, laws)
        self.assertTrue(valid)
        self.assertEqual(sources, ["VOS_308"])

    def test_source_only_fallback_passes_citation_verification(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "308 § 308. Tagatisraha. Üürnik võib maksta tagatisraha osadena.",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=False)

        text = ai.build_source_only_fallback("Kui suur võib tagatisraha olla?", laws)
        valid, sources = SourceVerifier().verify_sources(text, laws)

        self.assertTrue(valid)
        self.assertEqual(sources, ["VOS_308"])
        self.assertIn("Tagatisraha", text)

    def test_auxiliary_police_fine_fallback_is_focused_and_conditional(self):
        laws = [
            {
                "id": law_id,
                "title": law_id,
                "text": "Kontrollitud allika tekst.",
                "source": "Riigi Teataja",
                "domain": law_id.split("_", 1)[0],
            }
            for law_id in (
                "ABIPOLS_3",
                "ABIPOLS_16",
                "VTMS_19",
                "VTMS_54B2",
                "VTMS_54B5",
            )
        ]
        ai = OfflineAIService(allow_mock=False)

        text = ai.build_source_only_fallback(
            "Abipolitsei trahvis mind. Sain trahviteate.",
            laws,
        )
        valid, sources = SourceVerifier().verify_sources(text, laws)

        self.assertTrue(valid)
        self.assertEqual(
            sources,
            ["ABIPOLS_3", "ABIPOLS_16", "VTMS_54B2", "VTMS_54B5"],
        )
        self.assertIn("30 päeva", text)
        self.assertIn("ei saa veel kinnitada", text)
        self.assertIn("Kui dokumenti ei antud, küsi selle koopia", text)
        self.assertNotIn("VTMS_114", text)

    def test_latest_turn_fallback_covers_deadline_and_payment(self):
        laws = [
            {
                "id": "VTMS_118",
                "title": "Väärteomenetluse seadustik § 118",
                "text": "Tähtaja möödumisel tuleb esitada tähtaja ennistamise taotlus.",
                "source": "Riigi Teataja",
                "domain": "VTMS",
            },
            {
                "id": "KARS_66",
                "title": "Karistusseadustik § 66",
                "text": "Rahatrahvi võib mõjuvatel põhjustel määrata tasuda ositi.",
                "source": "Riigi Teataja",
                "domain": "KARS",
            },
            {
                "id": "VTMS_204",
                "title": "Väärteomenetluse seadustik § 204",
                "text": "Tähtajaks tasumata rahatrahv saadetakse kohtutäiturile.",
                "source": "Riigi Teataja",
                "domain": "VTMS",
            },
        ]
        ai = OfflineAIService(allow_mock=False)
        case = (
            "Sain rahatrahvi.\n\nKASUTAJA VIIMANE SÕNUM:\n"
            "Kaebe tähtaeg on möödas. Kas saan veel kaevata ja maksta osade kaupa?\n\n"
            "Vasta eeskätt viimasele sõnumile. Varasem tekst on ainult taust."
        )

        text = ai.build_source_only_fallback(case, laws)
        valid, sources = SourceVerifier().verify_sources(text, laws)

        self.assertTrue(valid)
        self.assertEqual(sources, ["VTMS_118", "KARS_66", "VTMS_204"])
        self.assertIn("tähtaja ennistamise", text)
        self.assertIn("tasumise ositi", text)
        self.assertIn("kohtutäiturile", text)

    def test_suffix_section_id_is_normalized(self):
        laws = [{
            "id": "TLS_4A",
            "title": "Töölepingu seadus § 4a",
            "text": "test",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=True)
        output = ai._prepare_output(
            "ÕIGUSLIK KOHALDAMINE:\nVäide [tls_4a].\n\nSOOVITUSED:\nKontrolli.\n\nKASUTATUD ALLIKAD: [tls_4a]",
            laws,
        )
        self.assertIn("[TLS_4A]", output)

    def test_superscript_section_id_is_normalized(self):
        laws = [{
            "id": "VOS_3B2",
            "title": "Võlaõigusseadus § 3²",
            "text": "test",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=True)
        output = ai._prepare_output(
            "ÕIGUSLIK KOHALDAMINE:\nVäide [vos_3b2].\n\nSOOVITUSED:\nKontrolli.\n\nKASUTATUD ALLIKAD: [vos_3b2]",
            laws,
        )
        self.assertIn("[VOS_3B2]", output)

    def test_structured_claims_get_inline_citation_on_every_sentence(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": (
                "Üürnik võib maksta tagatisraha osadena. "
                "Esimene osa tuleb tasuda pärast lepingu sõlmimist."
            ),
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=False)
        raw = json.dumps({
            "claims": [
                {
                    "text": "Üürnik võib maksta tagatisraha osadena.",
                    "source_id": "vos_308",
                    "evidence": "Üürnik võib maksta tagatisraha osadena.",
                },
                {
                    "text": "Esimene osa tuleb tasuda pärast lepingu sõlmimist.",
                    "source_id": "VOS_308",
                    "evidence": "Esimene osa tuleb tasuda pärast lepingu sõlmimist.",
                },
            ],
        }, ensure_ascii=False)

        output = ai._prepare_output(raw, laws)
        valid, sources = SourceVerifier().verify_sources(output, laws)

        self.assertTrue(valid)
        self.assertEqual(sources, ["VOS_308"])
        self.assertIn("osadena [VOS_308].", output)
        self.assertIn("sõlmimist [VOS_308].", output)

    def test_same_evidence_is_rendered_only_once(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Tagatisraha võib olla kuni kolme kuu üüri ulatuses.",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=False)
        raw = json.dumps({
            "claims": [
                {
                    "text": "Tagatisraha võib olla kuni kolme kuu üüri ulatuses.",
                    "source_id": "VOS_308",
                    "evidence": "Tagatisraha võib olla kuni kolme kuu üüri ulatuses.",
                },
                {
                    "text": "Üle kolme kuu tagatisraha ei ole selle sätte piirides.",
                    "source_id": "VOS_308",
                    "evidence": "Tagatisraha võib olla kuni kolme kuu üüri ulatuses.",
                },
            ],
        }, ensure_ascii=False)

        output = ai._prepare_output(raw, laws)

        self.assertEqual(output.count("[VOS_308]."), 1)

    def test_structured_claim_with_unknown_source_is_not_rendered(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Tagatisraha tingimusi tuleb kontrollida.",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=False)
        raw = json.dumps({
            "claims": [{
                "text": "Tagatisraha tuleb alati kohe täies ulatuses tasuda.",
                "source_id": "FAKE_999",
                "evidence": "Tagatisraha tingimused.",
            }],
        })

        self.assertEqual(ai._prepare_output(raw, laws), "")

    def test_retry_uses_same_strict_verifier_as_api_boundary(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Tagatisraha tingimusi tuleb kontrollida.",
            "source": "Riigi Teataja",
        }]
        settings = load_settings({
            "OLLAMA_CITATION_RETRIES": "1",
            "ALLOW_MOCK_ANALYSIS": "false",
        })
        ai = OfflineAIService(settings=settings)
        first_response = (
            "OLUKORD:\nVÕS reguleerib kirjeldatud olukorda.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Tagatisraha tingimusi tuleb kontrollida [VOS_308].\n\n"
            "SOOVITUSED:\nSäilita leping.\n\n"
            "KASUTATUD ALLIKAD: [VOS_308]"
        )
        repaired_response = json.dumps({
            "claims": [{
                "text": "Tagatisraha tingimusi tuleb kontrollida.",
                "source_id": "VOS_308",
                "evidence": "Tagatisraha tingimusi tuleb kontrollida.",
            }],
        }, ensure_ascii=False)

        with patch.object(
            ai, "_call_ollama", side_effect=[first_response, repaired_response]
        ) as call_ollama:
            output, is_mock = ai.analyze_case("Test", laws)

        valid, _ = SourceVerifier().verify_sources(output, laws)
        self.assertFalse(is_mock)
        self.assertTrue(valid)
        self.assertEqual(call_ollama.call_count, 2)

    def test_evidence_gate_replaces_extra_quantity_and_inference_with_source_text(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Üürnik võib tagatisraha maksta kolme kuu jooksul võrdsetes osades.",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=False)
        raw = json.dumps({
            "claims": [{
                "text": (
                    "Üürnik võib tagatisraha maksta kolme kuu jooksul võrdsetes "
                    "osades, mis tähendab, et nõue tuleb tasuda nelja kuuga."
                ),
                "source_id": "VOS_308",
                "evidence": (
                    "Üürnik võib tagatisraha maksta kolme kuu jooksul võrdsetes osades."
                ),
            }],
        }, ensure_ascii=False)

        output = ai._prepare_output(raw, laws)
        valid, sources = SourceVerifier().verify_sources(output, laws)

        self.assertTrue(valid)
        self.assertEqual(sources, ["VOS_308"])
        self.assertIn("kolme kuu jooksul", output)
        self.assertNotIn("nelja kuuga", output)
        self.assertNotIn("mis tähendab", output)

    def test_fabricated_evidence_is_not_rendered(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Üürnik võib tagatisraha maksta kolme kuu jooksul võrdsetes osades.",
            "source": "Riigi Teataja",
        }]
        ai = OfflineAIService(allow_mock=False)
        raw = json.dumps({
            "claims": [{
                "text": "Tagatisraha tuleb tasuda nelja kuuga.",
                "source_id": "VOS_308",
                "evidence": "Tagatisraha tuleb tasuda nelja kuuga.",
            }],
        }, ensure_ascii=False)

        self.assertEqual(ai._prepare_output(raw, laws), "")

    def test_ollama_payload_uses_central_settings(self):
        settings = load_settings({
            "OLLAMA_HOST": "http://example.test:11434",
            "OLLAMA_MODEL": "unit-model",
            "OLLAMA_TIMEOUT": "222",
            "OLLAMA_TEMPERATURE": "0.12",
            "OLLAMA_TOP_P": "0.77",
            "OLLAMA_NUM_CTX": "4096",
            "OLLAMA_NUM_PREDICT": "555",
            "OLLAMA_THINK": "false",
            "OLLAMA_KEEP_ALIVE": "15m",
        })
        ai = OfflineAIService(settings=settings)
        response = Mock(status_code=200)
        response.json.return_value = {"response": "OK"}

        with patch("services.offline_ai.requests.post", return_value=response) as post:
            result = ai._call_ollama("test prompt")

        self.assertEqual(result, "OK")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["timeout"], 222)
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "unit-model")
        self.assertEqual(payload["format"], AI_RESPONSE_SCHEMA)
        self.assertFalse(payload["think"])
        self.assertEqual(payload["keep_alive"], "15m")
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["options"]["num_predict"], 555)
        self.assertAlmostEqual(payload["options"]["temperature"], 0.12)
        self.assertAlmostEqual(payload["options"]["top_p"], 0.77)


if __name__ == "__main__":
    unittest.main()
