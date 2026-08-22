import asyncio
import base64
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from fastapi import HTTPException

import main
from services.offline_ai import OfflineAIService
from verifiers.source_verifier import SourceVerifier
from services.matters import MatterStore
from services.runtime_guard import RuntimeGuard


AUTH_HEADERS = (
    {main.ACCESS_CODE_HEADER: main.settings.app_access_code}
    if main.settings.app_access_code else {}
)


class MainTests(unittest.TestCase):
    def test_document_upload_matter_read_and_delete_are_local(self):
        payload = base64.b64encode(
            "Trahvisumma on 4000 eurot.".encode("utf-8")
        ).decode("ascii")
        with TestClient(main.app) as client:
            uploaded = client.post("/documents", headers=AUTH_HEADERS, json={
                "file_name": "otsus.txt",
                "content_base64": payload,
            })
            self.assertEqual(uploaded.status_code, 200)
            upload_data = uploaded.json()
            matter_id = upload_data["matter"]["matter_id"]
            self.assertEqual(upload_data["document"]["span_count"], 1)
            self.assertNotIn("content_base64", uploaded.text)

            matter = client.get(f"/matters/{matter_id}", headers=AUTH_HEADERS)
            self.assertEqual(matter.status_code, 200)
            self.assertEqual(len(matter.json()["documents"]), 1)

            deleted = client.delete(f"/matters/{matter_id}", headers=AUTH_HEADERS)
            self.assertEqual(deleted.json(), {"deleted": True})
            self.assertEqual(
                client.get(f"/matters/{matter_id}", headers=AUTH_HEADERS).status_code,
                404,
            )

    def test_document_upload_rejects_invalid_base64(self):
        with TestClient(main.app) as client:
            response = client.post("/documents", headers=AUTH_HEADERS, json={
                "file_name": "otsus.txt",
                "content_base64": "%%not-base64%%",
            })

        self.assertEqual(response.status_code, 400)

    def test_analysis_includes_verified_document_span_and_law(self):
        store = MatterStore()
        matter = store.create("Üüriasi")
        store.add_document(matter["matter_id"], {
            "document_id": "DOC-LEASE",
            "file_name": "leping.txt",
            "sha256": "abc",
            "file_type": "txt",
            "byte_size": 20,
            "page_count": 1,
            "text_length": 37,
            "extraction_method": "text",
            "warnings": [],
            "spans": [{
                "span_id": "DOC-LEASE-P1-S1",
                "document_id": "DOC-LEASE",
                "file_name": "leping.txt",
                "page": 1,
                "start": 0,
                "end": 37,
                "text": "Tagatisraha suurus on neli kuud.",
                "method": "text",
            }],
        })
        main.app.state.matter_store = store
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "Üürnik võib maksta tagatisraha osadena.",
            "source": "Riigi Teataja",
            "domain": "VOS",
        }]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["tagatisraha"],
                "domain_hints": ["VOS"],
                "section_hints": ["VOS_308"],
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "OLUKORD:\nDokumenti võrreldi seadusega.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Üürnik võib maksta tagatisraha osadena [VOS_308].\n\n"
            "SOOVITUSED:\nKontrolli lepingut.\n\n"
            "KASUTATUD ALLIKAD: [VOS_308]",
            False,
        )

        response = asyncio.run(main.analyze_case(
            main.CaseAnalysisRequest(
                case_description="Kas nelja kuu tagatisraha on lubatud?",
                current_message="Kas nelja kuu tagatisraha on lubatud?",
                matter_id=matter["matter_id"],
                document_ids=["DOC-LEASE"],
            ),
            legal_service,
            ai_service,
            SourceVerifier(),
        ))

        passed_case = ai_service.analyze_case.call_args.args[0]
        self.assertIn("KONTROLLITUD DOKUMENDIKATKENDID", passed_case)
        self.assertIn("DOC-LEASE-P1-S1", passed_case)
        self.assertEqual(response.claims[0].kind, "document_excerpt")
        self.assertEqual(
            response.claims[0].verification_status,
            "DOCUMENT_TEXT_VERIFIED",
        )
        self.assertEqual(response.claims[0].sources[0].page, 1)
        self.assertIn("VOS_308", response.sources_used)
        self.assertTrue(response.layered_answer["short_answer"])
        self.assertEqual(response.pipeline["status"], "completed")
        self.assertEqual(len(response.pipeline["stages"]), 7)
        self.assertEqual(response.legal_context["mode"], "LOCAL_CORPUS")
    def test_root_serves_cache_free_conversational_ui(self):
        with TestClient(main.app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertNotIn("Kuidas sinu küsimust mõistsin", response.text)
        self.assertNotIn('id="case-form"', response.text)
        self.assertIn('aria-label="Vestlus ÕigusAI-ga"', response.text)
        self.assertIn('id="chat-log"', response.text)
        self.assertIn('id="message-input"', response.text)
        self.assertIn("ühe lihtsa küsimuse korraga", response.text)
        self.assertIn("Vasta olemasoleva info põhjal", response.text)
        self.assertIn("Uus vestlus", response.text)
        self.assertIn("questionsAreSimilar", response.text)
        self.assertIn("requestsPersonalIdentifier", response.text)
        self.assertIn("Kontrollitud õigusallikad", response.text)
        self.assertIn("formatLawTitle", response.text)
        self.assertIn('id="attach-button"', response.text)
        self.assertIn("checkServiceHealth", response.text)
        self.assertIn("Dokumendi ja õiguse esmane võrdlus", response.text)
        self.assertIn('id="access-gate"', response.text)
        self.assertIn("Juhtumikaart", response.text)
        self.assertIn("Koosta dokumendikavand", response.text)
        self.assertIn("Kuidas vastus kontrolliti", response.text)
        self.assertIn("1 mainitud dokument", response.text)

    def test_intake_endpoint_returns_user_friendly_case_summary(self):
        intake_service = Mock()
        intake_service.understand.return_value = {
            "input_type": "fragment",
            "topic": "koondamine",
            "summary": "Kirjutasid teemaks „koondamine“.",
            "user_goal": "Abi eesmärk vajab täpsustamist.",
            "help_types": ["general_information"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": ["mis juhtus"],
            "clarification_questions": ["Mis täpselt juhtus?"],
            "ready_for_analysis": False,
            "search_query": "koondamine",
            "analysis_context": "Olukorra kokkuvõte: koondamine",
            "input_length": 10,
            "used_ai": False,
        }

        response = asyncio.run(main.understand_case(
            main.CaseIntakeRequest(case_description="koondamine"),
            intake_service,
        ))

        self.assertFalse(response.ready_for_analysis)
        self.assertEqual(response.clarification_questions, ["Mis täpselt juhtus?"])

    def test_exact_section_hint_filters_unrelated_model_context(self):
        laws = [
            {
                "id": "HMS_25",
                "title": "Haldusmenetluse seadus § 25",
                "text": "Dokument toimetatakse kätte postiga või elektrooniliselt.",
                "source": "Riigi Teataja",
                "domain": "HMS",
            },
            {
                "id": "TAIMKS_2B2",
                "title": "Taimekaitseseadus § 2²",
                "text": "Taimekaitse dokumentide erireegel.",
                "source": "Riigi Teataja",
                "domain": "TAIMKS",
            },
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["kattetoimetamine"],
                "domain_hints": ["HMS"],
                "section_hints": ["HMS_25"],
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "OLUKORD:\nKontrollitud vastus.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Dokumendi võib kätte toimetada postiga [HMS_25].\n\n"
            "SOOVITUSED:\nSäilita teated.\n\n"
            "KASUTATUD ALLIKAD: [HMS_25]",
            False,
        )

        response = asyncio.run(main.analyze_case(
            main.CaseAnalysisRequest(
                case_description="Kuidas võib haldusorgan otsuse kätte toimetada?",
                case_context="Kokkuvõte: kasutaja ootab otsust.",
            ),
            legal_service,
            ai_service,
            SourceVerifier(),
        ))

        passed_laws = ai_service.analyze_case.call_args.args[1]
        passed_case = ai_service.analyze_case.call_args.args[0]
        self.assertIn("Kuidas", passed_case)
        self.assertNotIn("Kokkuvõte", passed_case)
        self.assertEqual([law["id"] for law in passed_laws], ["HMS_25"])
        self.assertEqual([law.id for law in response.found_laws], ["HMS_25"])
        self.assertEqual(response.verification_status, "CITATIONS_VERIFIED")

    def test_analyze_returns_source_digest_when_local_model_fails(self):
        laws = [{
            "id": "VOS_308",
            "title": "Võlaõigusseadus § 308",
            "text": "308 § 308. Tagatisraha. Üürnik võib maksta tagatisraha osadena.",
            "source": "Riigi Teataja",
        }]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": [],
                "domain_hints": ["VOS"],
                "section_hints": ["VOS_308"],
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = OfflineAIService(allow_mock=False)
        request = main.CaseAnalysisRequest(
            case_description="Kui suur võib tagatisraha olla?"
        )

        with patch.object(
            ai_service,
            "analyze_case_structured",
            side_effect=RuntimeError("model unavailable"),
        ):
            response = asyncio.run(
                main.analyze_case(
                    request,
                    legal_service,
                    ai_service,
                    SourceVerifier(),
                )
            )

        self.assertEqual(response.verification_status, "SOURCE_ONLY_FALLBACK")
        self.assertEqual(response.sources_used, ["VOS_308"])
        self.assertIn("Tagatisraha", response.analysis)

    def test_oral_employment_termination_requires_tls_95_form_answer(self):
        laws = [
            {
                "id": "TLS_88",
                "title": "Töölepingu seadus § 88",
                "text": "Tööandja võib töölepingu erakorraliselt üles öelda töötajast tuleneval mõjuval põhjusel.",
                "source": "Riigi Teataja: TLS",
                "domain": "TLS",
            },
            {
                "id": "TLS_95",
                "title": "Töölepingu seadus § 95",
                "text": (
                    "Töölepingu võib üles öelda kirjalikku taasesitamist võimaldavas "
                    "vormis ülesütlemisavaldusega. Vorminõuet rikkudes tehtud "
                    "ülesütlemisavaldus on tühine. Tööandja peab ülesütlemist "
                    "põhjendama kirjalikku taasesitamist võimaldavas vormis."
                ),
                "source": "Riigi Teataja: TLS",
                "domain": "TLS",
            },
            {
                "id": "TLS_104",
                "title": "Töölepingu seadus § 104",
                "text": "Ülesütlemise tühisuse tuvastamiseks võib pöörduda töövaidlusorganisse.",
                "source": "Riigi Teataja: TLS",
                "domain": "TLS",
            },
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["töölepingu ülesütlemine", "suuline"],
                "domain_hints": ["TLS"],
                "section_hints": [],
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "OLUKORD:\nKüsimus puudutab töölepingu ülesütlemist.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\nTööandja võib lepingu mõjuval põhjusel "
            "üles öelda [TLS_88].\n\nSOOVITUSED:\nSäilita dokumendid.\n\n"
            "KASUTATUD ALLIKAD: [TLS_88]",
            False,
        )

        response = asyncio.run(main.analyze_case(
            main.CaseAnalysisRequest(
                case_description="Kas tööandja võib töölepingu suuliselt üles öelda?",
                current_message="Kas tööandja võib töölepingu suuliselt üles öelda?",
            ),
            legal_service,
            ai_service,
            SourceVerifier(),
        ))

        self.assertEqual(response.verification_status, "SOURCE_ONLY_FALLBACK")
        self.assertEqual(response.sources_used, ["TLS_95"])
        self.assertIn("kirjalikku taasesitamist võimaldavas vormis", response.analysis)
        self.assertIn("on tühine", response.analysis)
        self.assertIn("TLS §-l 95", response.warning)
        self.assertIn(
            "kirjalikku taasesitamist võimaldavas vormis",
            response.layered_answer["short_answer"],
        )

    def test_analyze_rejects_real_but_off_topic_fine_sources_before_model(self):
        laws = [{
            "id": "ABIPOLS_42",
            "title": "Abipolitseiniku seadus § 42",
            "text": "Abipolitseiniku staatusest vabastamine.",
            "source": "Riigi Teataja",
            "domain": "ABIPOLS",
        }]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["abipolitseinik"],
                "domain_hints": ["ABIPOLS"],
                "section_hints": [],
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(main.analyze_case(
                main.CaseAnalysisRequest(
                    case_description="Abipolitsei trahvis mind ilma asjata."
                ),
                legal_service,
                ai_service,
                SourceVerifier(),
            ))

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("trahvi", raised.exception.detail)
        ai_service.analyze_case.assert_not_called()

    def test_generic_trahviteade_does_not_force_motor_vehicle_warning_route(self):
        ids = [
            "ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_57", "VTMS_114",
            "VTMS_54B2", "VTMS_54B5",
        ]
        laws = [
            {
                "id": law_id,
                "title": law_id,
                "text": (
                    "Abipolitseiniku pädevus ja ülesanded."
                    if law_id.startswith("ABIPOLS")
                    else "Trahviteate ja väärteomenetluse vaidlustamise kord."
                ),
                "source": "Riigi Teataja",
                "domain": law_id.split("_", 1)[0],
            }
            for law_id in ids
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["trahviteate vaidlustamine"],
                "domain_hints": ["ABIPOLS", "VTMS"],
                "section_hints": [
                    "ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_57", "VTMS_114"
                ],
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "OLUKORD:\nKontrollitud vastus.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Abipolitseiniku pädevus on seaduses piiritletud [ABIPOLS_3].\n"
            "Kui saadud dokument on kiirmenetluse otsus, võib selle "
            "peale esitada kaebuse [VTMS_114].\n\n"
            "SOOVITUSED:\nKontrolli dokumenti.\n\n"
            "KASUTATUD ALLIKAD: [ABIPOLS_3] [VTMS_114]",
            False,
        )

        response = asyncio.run(main.analyze_case(
            main.CaseAnalysisRequest(
                case_description="Abipolitsei trahvis mind ja sain trahviteate.",
                search_query="Abipolitsei trahvis mind. Sain trahviteate.",
            ),
            legal_service,
            ai_service,
            SourceVerifier(),
        ))

        passed_ids = [law["id"] for law in ai_service.analyze_case.call_args.args[1]]
        self.assertNotIn("VTMS_54B2", passed_ids)
        self.assertNotIn("VTMS_54B5", passed_ids)
        self.assertIn("VTMS_57", passed_ids)
        self.assertIn("VTMS_114", passed_ids)
        self.assertEqual(response.verification_status, "CITATIONS_VERIFIED")

    def test_explicit_warning_notice_still_uses_its_own_route(self):
        ids = [
            "ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_57", "VTMS_114",
            "VTMS_54B2", "VTMS_54B5",
        ]
        laws = [
            {
                "id": law_id,
                "title": law_id,
                "text": (
                    "Abipolitseiniku pädevus ja ülesanded."
                    if law_id.startswith("ABIPOLS")
                    else "Hoiatustrahvi trahviteate ja väärteomenetluse kaebuse kord."
                ),
                "source": "Riigi Teataja",
                "domain": law_id.split("_", 1)[0],
            }
            for law_id in ids
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["hoiatustrahvi trahviteade"],
                "domain_hints": ["ABIPOLS", "VTMS"],
                "section_hints": ids,
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "OLUKORD:\nKontrollitud vastus.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Abipolitseiniku pädevus on seaduses piiritletud [ABIPOLS_3].\n"
            "Hoiatustrahvi trahviteate võib vaidlustada [VTMS_54B5].\n\n"
            "SOOVITUSED:\nKontrolli dokumenti.\n\n"
            "KASUTATUD ALLIKAD: [ABIPOLS_3] [VTMS_54B5]",
            False,
        )

        response = asyncio.run(main.analyze_case(
            main.CaseAnalysisRequest(
                case_description=(
                    "Abipolitsei trahvis mind. Dokument ütleb, et see on "
                    "mootorsõiduki eest vastutava isiku hoiatustrahvi trahviteade."
                ),
            ),
            legal_service,
            ai_service,
            SourceVerifier(),
        ))

        passed_ids = [law["id"] for law in ai_service.analyze_case.call_args.args[1]]
        self.assertIn("VTMS_54B2", passed_ids)
        self.assertIn("VTMS_54B5", passed_ids)
        self.assertNotIn("VTMS_57", passed_ids)
        self.assertEqual(response.verification_status, "CITATIONS_VERIFIED")

    def test_latest_turn_routes_missed_deadline_and_payment_together(self):
        ids = ["VTMS_114", "VTMS_118", "KARS_66", "VTMS_57", "VTMS_74", "VTMS_204"]
        texts = {
            "VTMS_114": "Rahatrahvi otsuse peale võib esitada maakohtule kaebuse.",
            "VTMS_118": "Kaebuse tähtaja möödumisel tuleb esitada tähtaja ennistamise taotlus.",
            "KARS_66": "Rahatrahvi võib mõjuvatel põhjustel määrata tasuda ositi.",
            "VTMS_57": "Kiirmenetluse otsuses märgitakse rahatrahvi tasumine ositi.",
            "VTMS_74": "Kohtuvälise menetleja otsuses märgitakse rahatrahvi tasumine ositi.",
            "VTMS_204": "Osastatud rahatrahvi osamaksed tuleb tasuda tähtaegselt.",
        }
        laws = [
            {
                "id": law_id,
                "title": law_id,
                "text": texts[law_id],
                "source": "Riigi Teataja",
                "domain": law_id.split("_", 1)[0],
            }
            for law_id in ids
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["tähtaja ennistamine", "rahatrahvi tasumine ositi"],
                "domain_hints": ["KARS", "VTMS"],
                "section_hints": ids,
                "matches": [],
                "notes": [],
            }),
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "OLUKORD:\nKontrollitud vastus.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Kaebuse tähtaja ennistamist võib taotleda [VTMS_118].\n"
            "Rahatrahvi võib mõjuvatel põhjustel määrata tasuda ositi [KARS_66].\n\n"
            "SOOVITUSED:\nKontrolli dokumendi pealkirja.\n\n"
            "KASUTATUD ALLIKAD: [VTMS_118] [KARS_66]",
            False,
        )
        current = (
            "Kaebe tähtaeg on möödas. Kas saan veel kaevata ja "
            "4000 eurot maksta osade kaupa?"
        )

        response = asyncio.run(main.analyze_case(
            main.CaseAnalysisRequest(
                case_description="Sain abipolitseilt rahatrahvi. " + current,
                current_message="Ma ei tea.",
                search_query=(
                    "rahatrahv väärteomenetlus kaebuse tähtaja ennistamise taotlus "
                    "rahatrahvi tasumine ositi"
                ),
                answer_requirements=[
                    "kas ja millistel tingimustel saab möödunud tähtaega ennistada",
                    "kas rahatrahvi saab tasuda ositi ja kellele taotlus esitada",
                ],
            ),
            legal_service,
            ai_service,
            SourceVerifier(),
        ))

        passed_ids = [law["id"] for law in ai_service.analyze_case.call_args.args[1]]
        passed_case = ai_service.analyze_case.call_args.args[0]
        self.assertIn("VTMS_118", passed_ids)
        self.assertIn("KARS_66", passed_ids)
        self.assertIn("VTMS_204", passed_ids)
        self.assertNotIn("VTMS_54B5", passed_ids)
        self.assertIn("KASUTAJA VIIMANE SÕNUM", passed_case)
        self.assertIn("VASTUS PEAB KÄSITLEMA", passed_case)
        self.assertEqual(response.verification_status, "CITATIONS_VERIFIED")

    def test_missing_corpus_keeps_app_alive_but_degraded(self):
        with TemporaryDirectory() as tmp:
            isolated = replace(
                main.settings,
                legal_data_file=Path(tmp) / "missing-laws.json",
            )
            with patch.object(main, "settings", isolated):
                with TestClient(main.app) as client:
                    response = client.get("/health")
                    self.assertEqual(response.status_code, 200)
                    data = response.json()
                    self.assertEqual(data["status"], "degraded")
                    self.assertFalse(data["legal_corpus_ready"])

    def test_health_reports_hybrid_retrieval_state(self):
        with TestClient(main.app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("hybrid_enabled", data)
        self.assertIn("hybrid_ready", data)
        self.assertIn("embedding_model", data)
        self.assertIn("embedding_dimension", data)
        self.assertIn("vector_rows", data)
        self.assertIn("hybrid_error", data)
        self.assertIn("reranker_enabled", data)
        self.assertIn("reranker_loaded", data)
        self.assertIn("reranker_ready", data)
        self.assertIn("reranker_model", data)
        self.assertIn("reranker_device", data)
        self.assertIn("reranker_error", data)
        self.assertEqual(data["version"], "0.9.1")
        self.assertIn("ready_for_demo", data)
        self.assertIn("analysis_model_ready", data)
        self.assertIn("ocr_model_ready", data)
        self.assertEqual(data["document_privacy"], "memory_only")
        self.assertIn("access_protected", data)
        self.assertIn("work_queue", data)
        self.assertIn("verified_live_context", data)
        self.assertIn("configured", data["verified_live_context"])
        self.assertTrue(
            data["capabilities"]["v11_6_verified_live_pilot_observability"]
        )
        self.assertEqual(data["matter_ttl_minutes"], main.settings.matter_ttl_minutes)

    def test_access_check_rejects_wrong_code_and_accepts_right_code(self):
        with TestClient(main.app) as client:
            main.app.state.runtime_guard = RuntimeGuard(
                access_code="turvaline-test-kood"
            )
            rejected = client.post(
                "/access/check",
                headers={main.ACCESS_CODE_HEADER: "vale-kood"},
            )
            accepted = client.post(
                "/access/check",
                headers={main.ACCESS_CODE_HEADER: "turvaline-test-kood"},
            )

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["ok"])

    def test_security_headers_are_added_to_browser_response(self):
        with TestClient(main.app) as client:
            response = client.get("/")

        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    def test_analyze_returns_503_without_corpus(self):
        with TemporaryDirectory() as tmp:
            isolated = replace(
                main.settings,
                legal_data_file=Path(tmp) / "missing-laws.json",
            )
            with patch.object(main, "settings", isolated):
                with TestClient(main.app) as client:
                    response = client.post(
                        "/analyze",
                        headers=AUTH_HEADERS,
                        json={"case_description": "Testjuhtum"},
                    )
                    self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
