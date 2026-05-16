"""Phase UI-5 — tests for the form parser service."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from sentinel.ui.services.form_parser import (  # noqa: E402
    loc_to_field_name,
    parse_form_to_dict,
    split_csv_ints,
    split_lines,
)


class TestParseFormToDict:
    def test_flat_keys(self) -> None:
        result = parse_form_to_dict({"foo": "1", "bar": "2"})
        assert result == {"foo": "1", "bar": "2"}

    def test_dot_separated_keys_nest(self) -> None:
        result = parse_form_to_dict({
            "meta.project": "test",
            "meta.base_url": "https://x",
        })
        assert result == {
            "meta": {"project": "test", "base_url": "https://x"},
        }

    def test_bracket_indices_become_list_items(self) -> None:
        result = parse_form_to_dict({
            "endpoints[0].path": "/a",
            "endpoints[0].method": "GET",
            "endpoints[1].path": "/b",
            "endpoints[1].method": "POST",
        })
        assert result == {
            "endpoints": [
                {"path": "/a", "method": "GET"},
                {"path": "/b", "method": "POST"},
            ],
        }

    def test_sparse_indices_fill_with_empty_dicts(self) -> None:
        """Index 2 alone produces a list of length 3 with two empty dicts."""
        result = parse_form_to_dict({"endpoints[2].path": "/c"})
        assert result == {
            "endpoints": [{}, {}, {"path": "/c"}],
        }

    def test_deep_nesting(self) -> None:
        result = parse_form_to_dict({
            "checks.transport.enabled": "true",
            "checks.transport.min_tls_version": "1.2",
            "checks.headers.required": "Header-A\nHeader-B",
        })
        assert result == {
            "checks": {
                "transport": {
                    "enabled": "true",
                    "min_tls_version": "1.2",
                },
                "headers": {
                    "required": "Header-A\nHeader-B",
                },
            },
        }

    def test_empty_form_returns_empty_dict(self) -> None:
        assert parse_form_to_dict({}) == {}


class TestSplitLines:
    def test_splits_on_newlines(self) -> None:
        assert split_lines("a\nb\nc") == ["a", "b", "c"]

    def test_strips_whitespace(self) -> None:
        assert split_lines("  a  \n\tb\t\n c ") == ["a", "b", "c"]

    def test_drops_empty_lines(self) -> None:
        assert split_lines("a\n\n\nb\n") == ["a", "b"]

    def test_handles_crlf(self) -> None:
        assert split_lines("a\r\nb\r\n") == ["a", "b"]

    def test_none_returns_empty(self) -> None:
        assert split_lines(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert split_lines("") == []


class TestSplitCsvInts:
    def test_all_ints(self) -> None:
        assert split_csv_ints("1, 2, 3") == [1, 2, 3]

    def test_mixed_int_and_string(self) -> None:
        assert split_csv_ints("1, alpha, 3") == [1, "alpha", 3]

    def test_strips_whitespace(self) -> None:
        assert split_csv_ints("  1 , 2 ,3") == [1, 2, 3]

    def test_drops_empty_tokens(self) -> None:
        assert split_csv_ints("1,,2,,") == [1, 2]

    def test_none_returns_empty(self) -> None:
        assert split_csv_ints(None) == []


class TestLocToFieldName:
    def test_single_str(self) -> None:
        assert loc_to_field_name(("meta",)) == "meta"

    def test_nested_strs(self) -> None:
        assert loc_to_field_name(("meta", "base_url")) == "meta.base_url"

    def test_with_list_index(self) -> None:
        assert (
            loc_to_field_name(("endpoints", 0, "path"))
            == "endpoints[0].path"
        )

    def test_list_index_at_end(self) -> None:
        assert loc_to_field_name(("endpoints", 2)) == "endpoints[2]"
