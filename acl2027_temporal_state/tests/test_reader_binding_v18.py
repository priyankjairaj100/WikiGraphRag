"""Authored-only blank-block amendment controls; no natural input access."""
from copy import deepcopy
import inspect
import json
import unittest

from temporal_state import reader_binding_v16 as v16
from temporal_state import reader_binding_v18 as v18
from test_reader_binding_v16 import authored_fact, payload, proposal


def amended_payload(*items, texts=()):
    raw = payload(*items)
    raw["schema_version"] = "reader_evidence_pack_v18"
    raw["blocks"] = [{"handle": "b" + str(i), "text": text}
                     for i, text in enumerate(texts)]
    return raw


def empty_payload(texts=()):
    raw = amended_payload(texts=texts)
    raw["facts"] = []
    raw["witnesses"] = []
    return raw


class BlankBlockTests(unittest.TestCase):
    def test_exact_empty_and_unicode_whitespace_roundtrip(self):
        texts = ("", " ", "\t\r\n", "\u00a0", "\u2003\u202f", "  Revenue\u00a0 ", "")
        raw = amended_payload(texts=texts)
        pack = v18.EvidencePack(raw)
        self.assertEqual(pack.snapshot(), raw)
        restored = v18.EvidencePack.from_prompt_view(pack.prompt_view())
        self.assertEqual(restored.as_json(), pack.as_json())
        self.assertEqual(restored.sha256, pack.sha256)
        self.assertEqual([b["text"] for b in restored.snapshot()["blocks"]], list(texts))

    def test_blank_block_handles_order_and_count_survive_text_interning(self):
        pack = v18.EvidencePack(empty_payload(("", "", "\u00a0", "", "\u00a0")))
        view = pack.prompt_view()
        self.assertEqual(len(view["texts"]), 2)
        self.assertEqual(len(view["blocks"]), 5)
        self.assertEqual([b[0] for b in view["blocks"]], ["b0", "b1", "b2", "b3", "b4"])
        self.assertEqual(v18.EvidencePack.from_prompt_view(view).snapshot(), pack.snapshot())

    def test_exact_64_blank_blocks_accepted_65_rejected(self):
        pack = v18.EvidencePack(empty_payload(("",) * 64))
        self.assertEqual(len(pack.snapshot()["blocks"]), 64)
        self.assertEqual(len(pack.prompt_view()["blocks"]), 64)
        self.assertEqual(v18.EvidencePack.from_prompt_view(pack.prompt_view()).sha256, pack.sha256)
        with self.assertRaisesRegex(v18.BindingError, "blocks_limit"):
            v18.EvidencePack(empty_payload(("",) * 65))

    def test_prompt_blank_rows_cannot_bypass_block_cap(self):
        view = v18.EvidencePack(empty_payload(("",) * 64)).prompt_view()
        view["blocks"].append(["b64", view["blocks"][0][1]])
        with self.assertRaisesRegex(v18.BindingError, "blocks_limit"):
            v18.EvidencePack.from_prompt_view(view)

    def test_whitespace_is_charged_to_exact_utf8_input_budget(self):
        raw = empty_payload(("",))
        overhead = len(v18.EvidencePack(raw).as_json().encode("utf-8"))
        # NBSP occupies two UTF-8 bytes; complete serialized pack is exactly the cap.
        available = 2_000_000 - overhead
        raw["blocks"][0]["text"] = "\u00a0" * (available // 2) + " " * (available % 2)
        pack = v18.EvidencePack(raw)
        self.assertEqual(len(pack.as_json().encode("utf-8")), 2_000_000)
        raw["blocks"][0]["text"] += " "
        with self.assertRaisesRegex(v18.BindingError, "json_size_limit"):
            v18.EvidencePack(raw)

    def test_blank_handle_and_json_overhead_are_charged(self):
        first = v18.EvidencePack(empty_payload(("",)))
        second = v18.EvidencePack(empty_payload(("", "")))
        self.assertGreater(len(second.as_json().encode("utf-8")), len(first.as_json().encode("utf-8")))
        self.assertGreater(len(json.dumps(second.prompt_view()).encode("utf-8")),
                           len(json.dumps(first.prompt_view()).encode("utf-8")))

    def test_nonstring_block_text_still_rejected(self):
        for value in (None, False, 0, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(v18.BindingError, "block_text"):
                v18.EvidencePack(empty_payload((value,)))

    def test_duplicate_blank_handle_rejected(self):
        raw = empty_payload(("", "\u00a0"))
        raw["blocks"][1]["handle"] = raw["blocks"][0]["handle"]
        with self.assertRaisesRegex(v18.BindingError, "duplicate_handle"):
            v18.EvidencePack(raw)

    def test_blank_handle_cannot_collide_with_witness_or_fact(self):
        for handle in ("f01", "w010"):
            raw = amended_payload(texts=("",))
            raw["blocks"][0]["handle"] = handle
            with self.subTest(handle=handle), self.assertRaisesRegex(v18.BindingError, "duplicate_handle"):
                v18.EvidencePack(raw)

    def test_prompt_duplicate_and_unused_registry_rows_rejected(self):
        view = v18.EvidencePack(empty_payload(("",))).prompt_view()
        for extra, code in ((deepcopy(view["texts"][0]), "duplicate_registry_id"),
                            (["t999", ""], "noncanonical_or_unused_prompt_content")):
            altered = deepcopy(view)
            altered["texts"].append(extra)
            with self.subTest(code=code), self.assertRaisesRegex(v18.BindingError, code):
                v18.EvidencePack.from_prompt_view(altered)

    def test_prompt_duplicate_blank_block_handle_rejected(self):
        view = v18.EvidencePack(empty_payload(("", "\u00a0"))).prompt_view()
        view["blocks"][1][0] = view["blocks"][0][0]
        with self.assertRaisesRegex(v18.BindingError, "duplicate_handle"):
            v18.EvidencePack.from_prompt_view(view)

    def test_blank_witness_text_remains_rejected(self):
        for text in ("", " ", "\u00a0", "\t\n\u2003"):
            raw = amended_payload(texts=(text,))
            raw["witnesses"][0]["text"] = text
            with self.subTest(text=text), self.assertRaisesRegex(v18.BindingError, "witness_text"):
                v18.EvidencePack(raw)

    def test_prompt_blank_text_cannot_be_reused_as_witness(self):
        pack = v18.EvidencePack(amended_payload(texts=("\u00a0",)))
        view = pack.prompt_view()
        view["witnesses"][0][2] = view["blocks"][0][1]
        with self.assertRaisesRegex(v18.BindingError, "witness_text"):
            v18.EvidencePack.from_prompt_view(view)

    def test_blank_fact_label_remains_rejected(self):
        for text in ("", " ", "\u00a0"):
            raw = amended_payload(texts=(text,))
            raw["facts"][0]["labels"] = [text]
            with self.subTest(text=text), self.assertRaisesRegex(v18.BindingError, "fact_label"):
                v18.EvidencePack(raw)

    def test_prompt_blank_text_cannot_be_reused_as_fact_label(self):
        pack = v18.EvidencePack(amended_payload(texts=("",)))
        view = pack.prompt_view()
        view["facts"][0][2] = [view["blocks"][0][1]]
        with self.assertRaisesRegex(v18.BindingError, "fact_label"):
            v18.EvidencePack.from_prompt_view(view)

    def test_nonempty_aspect_text_rules_remain_strict(self):
        paths = (("entity", "scheme"), ("entity", "identifier"), ("source", "version"),
                 ("population", "label"), ("period", "lexemes", "startDate"),
                 ("period", "lexemes", "endDate"))
        for path in paths:
            raw = amended_payload(texts=("",))
            current = raw["facts"][0]["bindings"][path[0]]["value"]
            for key in path[1:-1]:
                current = current[key]
            current[path[-1]] = "\u00a0"
            with self.subTest(path=path), self.assertRaises(v18.BindingError):
                v18.EvidencePack(raw)

    def test_blank_block_handle_is_not_a_binding_witness(self):
        raw = amended_payload(texts=("",))
        raw["facts"][0]["bindings"]["concept"]["witnesses"] = ["b0"]
        with self.assertRaisesRegex(v18.BindingError, "unavailable_binding_witness"):
            v18.EvidencePack(raw)

    def test_blank_blocks_do_not_supply_missing_support(self):
        raw = amended_payload(texts=("", "\u00a0"))
        raw["facts"][0]["bindings"]["population"] = None
        raw["witnesses"] = [w for w in raw["witnesses"] if w["aspect"] != "population"]
        pack = v18.EvidencePack(raw)
        self.assertEqual(v18.render_proposal(pack, proposal(pack))["action"], "insufficient")
        proposed = proposal(pack)
        proposed["hypotheses"][0]["claims"][0]["binding_witnesses"]["population"] = ["b0"]
        self.assertEqual(v18.render_proposal(pack, proposed)["action"], "invalid_output")

    def test_blank_blocks_alone_cannot_authorize_an_answer(self):
        pack = v18.EvidencePack(empty_payload(("", "\u00a0")))
        result = v18.render_proposal(pack, v18.lexical_baseline(pack, "Revenue for 2024"))
        self.assertEqual(result["action"], "insufficient")
        self.assertEqual(result["quantities"], [])

    def test_v16_still_rejects_blanks_and_cross_version_schemas(self):
        for text in ("", "\u00a0"):
            raw = amended_payload(texts=(text,))
            with self.assertRaisesRegex(v16.BindingError, "pack_schema"):
                v16.EvidencePack(raw)
            raw["schema_version"] = "reader_evidence_pack_v16"
            with self.assertRaisesRegex(v16.BindingError, "block_text"):
                v16.EvidencePack(raw)
            with self.assertRaisesRegex(v18.BindingError, "pack_schema"):
                v18.EvidencePack(raw)
        v16_view = v16.EvidencePack(payload()).prompt_view()
        v18_view = v18.EvidencePack(amended_payload()).prompt_view()
        with self.assertRaisesRegex(v18.BindingError, "prompt_schema"):
            v18.EvidencePack.from_prompt_view(v16_view)
        with self.assertRaisesRegex(v16.BindingError, "prompt_schema"):
            v16.EvidencePack.from_prompt_view(v18_view)


class NonemptyEquivalenceTests(unittest.TestCase):
    def test_nonempty_pack_and_projection_differ_only_in_version(self):
        old = payload(authored_fact(), authored_fact("f02", source="b", year="2023"))
        old["blocks"] = [{"handle": "b01", "text": "  Authored table\u00a0\nRevenue 12 "}]
        new = deepcopy(old)
        new["schema_version"] = "reader_evidence_pack_v18"
        pack16, pack18 = v16.EvidencePack(old), v18.EvidencePack(new)
        expected = pack16.snapshot()
        expected["schema_version"] = "reader_evidence_pack_v18"
        self.assertEqual(pack18.snapshot(), expected)
        expected_view = pack16.prompt_view()
        expected_view["schema_version"] = "reader_evidence_prompt_v18"
        self.assertEqual(pack18.prompt_view(), expected_view)
        self.assertEqual(v18.EvidencePack.from_prompt_view(expected_view).snapshot(), expected)

    def test_b0_b1_b2_outputs_match_for_authored_nonempty_cases(self):
        scenarios = ((authored_fact(),),
                     (authored_fact(), authored_fact("f02", year="2023", source="b")),
                     (authored_fact(), authored_fact("f02", value="13")),
                     (authored_fact(), authored_fact("f02", value=None)))
        for items in scenarios:
            old = payload(*items)
            old["blocks"] = [{"handle": "b01", "text": "Authored complete block"}]
            new = deepcopy(old)
            new["schema_version"] = "reader_evidence_pack_v18"
            pack16, pack18 = v16.EvidencePack(old), v18.EvidencePack(new)
            for question in ("Alpha revenue for 2024", "Alpha revenue for both 2023 and 2024", "Alpha turnover"):
                with self.subTest(items=len(items), question=question):
                    self.assertEqual(v16.lexical_baseline(pack16, question), v18.lexical_baseline(pack18, question))
            for output in (proposal(pack16), proposal(pack16, unknown=True),
                           proposal(pack16, decision="insufficient")):
                self.assertEqual(v16.validate_proposal(pack16, output), v18.validate_proposal(pack18, output))
                self.assertEqual(v16.render_proposal(pack16, output), v18.render_proposal(pack18, output))
            claim = proposal(pack16)["hypotheses"][0]["claims"][0]
            for value in ("12", "99"):
                direct = {"action": "answered", "unknown": False, "clarify_aspects": [],
                          "quantities": [{"value": value, **claim}]}
                self.assertEqual(v16.validate_direct_output(pack16, direct), v18.validate_direct_output(pack18, direct))
                self.assertEqual(v16.score_direct_quantities(pack16, direct), v18.score_direct_quantities(pack18, direct))

    def test_blank_addition_does_not_change_automatic_baseline_or_finalization(self):
        plain = v18.EvidencePack(amended_payload())
        blank = v18.EvidencePack(amended_payload(texts=("", "\u00a0", " ")))
        self.assertEqual(v18.lexical_baseline(plain, "Alpha revenue for 2024"),
                         v18.lexical_baseline(blank, "Alpha revenue for 2024"))
        self.assertEqual(v18.render_proposal(plain, proposal(plain)),
                         v18.render_proposal(blank, proposal(blank)))

    def test_every_module_function_and_frozen_lexical_constant_is_unchanged(self):
        # This amendment promises no change to helper/baseline/finalizer code.
        old_functions = {name: value for name, value in vars(v16).items()
                         if inspect.isfunction(value) and value.__module__ == v16.__name__}
        new_functions = {name: value for name, value in vars(v18).items()
                         if inspect.isfunction(value) and value.__module__ == v18.__name__}
        self.assertEqual(set(old_functions), set(new_functions))
        for name in old_functions:
            with self.subTest(name=name):
                self.assertEqual(inspect.getsource(old_functions[name]), inspect.getsource(new_functions[name]))
        for name in ("ASPECTS", "MAX_FACTS", "MAX_HYPOTHESES", "MAX_CLAIMS",
                     "STOP_WORDS", "MULTI_WORDS", "UNIT_WORDS"):
            self.assertEqual(getattr(v16, name), getattr(v18, name))

    def test_existing_forever_empty_lexeme_sentinel_is_unchanged(self):
        raw = amended_payload(texts=("",))
        raw["facts"][0]["bindings"]["period"]["value"] = {"kind": "forever", "lexemes": {"forever": ""}}
        pack = v18.EvidencePack(raw)
        self.assertEqual(v18.EvidencePack.from_prompt_view(pack.prompt_view()).sha256, pack.sha256)
        raw["facts"][0]["bindings"]["period"]["value"]["lexemes"]["forever"] = " "
        with self.assertRaisesRegex(v18.BindingError, "forever_lexeme"):
            v18.EvidencePack(raw)


if __name__ == "__main__":
    unittest.main()
