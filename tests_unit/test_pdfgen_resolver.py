"""Pure-Python unit tests for the resolver — no Odoo dependency."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

MODELS_DIR = Path(__file__).parent.parent / "pdfgeneratorapi_connector" / "models"
spec = importlib.util.spec_from_file_location(
    "pdfgen_resolver",
    MODELS_DIR / "pdfgen_resolver.py",
)
resolver = importlib.util.module_from_spec(spec)
sys.modules["pdfgen_resolver"] = resolver
spec.loader.exec_module(resolver)


def _line(placeholder, odoo_path="", is_list=False, children=None):
    return SimpleNamespace(
        placeholder_path=placeholder,
        odoo_field_path=odoo_path,
        is_list=is_list,
        child_lines=children or [],
    )


class FlattenPlaceholdersTests(unittest.TestCase):
    def test_scalars(self):
        out = list(resolver.flatten_placeholders({"a": "", "b": 0}))
        self.assertIn(("a", "scalar", None), out)
        self.assertIn(("b", "scalar", None), out)

    def test_nested_dict_is_dotted(self):
        out = list(resolver.flatten_placeholders({"totals": {"net": "", "tax": ""}}))
        paths = [row[0] for row in out]
        self.assertIn("totals.net", paths)
        self.assertIn("totals.tax", paths)

    def test_array_of_dicts_yields_list_kind_with_sample(self):
        out = list(
            resolver.flatten_placeholders(
                {"lines": [{"desc": "", "qty": 0}, {"desc": "", "qty": 0}]}
            )
        )
        self.assertEqual(len(out), 1)
        path, kind, sample = out[0]
        self.assertEqual(path, "lines")
        self.assertEqual(kind, "list")
        self.assertEqual(sample, {"desc": "", "qty": 0})

    def test_empty_list_is_scalar(self):
        out = list(resolver.flatten_placeholders({"lines": []}))
        self.assertEqual(out, [("lines", "scalar", None)])

    def test_non_dict_input_yields_nothing(self):
        self.assertEqual(list(resolver.flatten_placeholders("not a dict")), [])

    def test_nested_list_inside_dict_is_reported_relative(self):
        out = list(resolver.flatten_placeholders({"page": {"lines": [{"x": ""}]}}))
        path, kind, sample = out[0]
        self.assertEqual(path, "page.lines")
        self.assertEqual(kind, "list")
        self.assertEqual(sample, {"x": ""})


class SetNestedTests(unittest.TestCase):
    def test_flat_key(self):
        target = {}
        resolver.set_nested(target, "a", 1)
        self.assertEqual(target, {"a": 1})

    def test_dotted_key_creates_nested_dicts(self):
        target = {}
        resolver.set_nested(target, "a.b.c", 7)
        self.assertEqual(target, {"a": {"b": {"c": 7}}})

    def test_overwrites_non_dict_branch(self):
        target = {"a": "was-a-string"}
        resolver.set_nested(target, "a.b", 5)
        self.assertEqual(target, {"a": {"b": 5}})


class WalkTests(unittest.TestCase):
    def test_empty_path_returns_record(self):
        rec = SimpleNamespace(name="x")
        self.assertIs(resolver.walk(rec, ""), rec)

    def test_follows_attribute_chain(self):
        rec = SimpleNamespace(partner=SimpleNamespace(name="Acme"))
        self.assertEqual(resolver.walk(rec, "partner.name"), "Acme")

    def test_missing_attr_returns_none(self):
        rec = SimpleNamespace()
        self.assertIsNone(resolver.walk(rec, "nope.nope"))

    def test_false_anywhere_short_circuits(self):
        rec = SimpleNamespace(partner=False)
        self.assertIsNone(resolver.walk(rec, "partner.name"))

    def test_dict_traversal(self):
        rec = {"customer": {"name": "Acme"}}
        self.assertEqual(resolver.walk(rec, "customer.name"), "Acme")


class ResolveTests(unittest.TestCase):
    def test_flat_scalars(self):
        rec = SimpleNamespace(name="INV/001", amount=100)
        lines = [_line("invoice_number", "name"), _line("total", "amount")]
        self.assertEqual(
            resolver.resolve(rec, lines),
            {"invoice_number": "INV/001", "total": 100},
        )

    def test_nested_placeholder_path(self):
        rec = SimpleNamespace(num="X", amt=1)
        lines = [_line("totals.gross", "amt"), _line("header.number", "num")]
        self.assertEqual(
            resolver.resolve(rec, lines),
            {"totals": {"gross": 1}, "header": {"number": "X"}},
        )

    def test_list_iteration(self):
        lines_rs = [
            SimpleNamespace(description="A", quantity=2),
            SimpleNamespace(description="B", quantity=5),
        ]
        rec = SimpleNamespace(lines=lines_rs)
        mapping_lines = [
            _line(
                "items",
                "lines",
                is_list=True,
                children=[
                    _line("desc", "description"),
                    _line("qty", "quantity"),
                ],
            )
        ]
        self.assertEqual(
            resolver.resolve(rec, mapping_lines),
            {
                "items": [
                    {"desc": "A", "qty": 2},
                    {"desc": "B", "qty": 5},
                ]
            },
        )

    def test_list_with_empty_recordset(self):
        rec = SimpleNamespace(lines=[])
        mapping_lines = [_line("items", "lines", is_list=True, children=[_line("x", "x")])]
        self.assertEqual(resolver.resolve(rec, mapping_lines), {"items": []})

    def test_list_when_path_returns_empty_string(self):
        rec = SimpleNamespace()
        mapping_lines = [_line("items", "missing", is_list=True, children=[_line("x", "x")])]
        self.assertEqual(resolver.resolve(rec, mapping_lines), {"items": []})

    def test_blank_odoo_path_yields_empty_string(self):
        rec = SimpleNamespace(name="X")
        self.assertEqual(
            resolver.resolve(rec, [_line("placeholder_a", "")]),
            {"placeholder_a": ""},
        )

    def test_date_like_value_is_isoformatted(self):
        import datetime

        rec = SimpleNamespace(d=datetime.date(2026, 4, 21))
        self.assertEqual(
            resolver.resolve(rec, [_line("date", "d")]),
            {"date": "2026-04-21"},
        )

    def test_recordset_like_value_uses_display_name(self):
        rs = SimpleNamespace(_name="res.partner", display_name="Acme Corp", ids=[1])
        rec = SimpleNamespace(partner=rs)
        self.assertEqual(
            resolver.resolve(rec, [_line("customer", "partner")]),
            {"customer": "Acme Corp"},
        )

    def test_empty_recordset_becomes_empty_string(self):
        rs = SimpleNamespace(_name="res.partner", ids=[], display_name="")
        rec = SimpleNamespace(partner=rs)
        self.assertEqual(
            resolver.resolve(rec, [_line("customer", "partner")]),
            {"customer": ""},
        )
