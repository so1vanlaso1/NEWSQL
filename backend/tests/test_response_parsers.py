"""Phase 7: the defensive LLM response parser (backend/llm/response_parser.py).

The parser must never raise on model output: fenced/embedded/malformed JSON, non-object
payloads, and partial field failures all resolve to a usable ``LlmDecision`` rather than an
exception (plan §26, normal-SQL JSON boundary).
"""
import json

from backend.llm import response_parser as p


# ---- extract_json_object ----------------------------------------------------
def test_extract_plain_object():
    assert p.extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_fenced_object():
    text = "```json\n{\"a\": 1, \"b\": \"x\"}\n```"
    assert p.extract_json_object(text) == {"a": 1, "b": "x"}


def test_extract_embedded_in_prose():
    text = 'Sure, here it is: {"sql": "SELECT 1"} — hope that helps!'
    assert p.extract_json_object(text) == {"sql": "SELECT 1"}


def test_extract_balanced_object_ignores_braces_in_strings():
    text = '{"note": "a } brace in a string", "n": 2}'
    assert p.extract_json_object(text) == {"note": "a } brace in a string", "n": 2}


def test_extract_malformed_returns_none():
    assert p.extract_json_object("not json at all") is None


def test_extract_non_object_returns_none():
    assert p.extract_json_object("[1, 2, 3]") is None


def test_extract_empty_returns_none():
    assert p.extract_json_object("") is None


# ---- clean_sql --------------------------------------------------------------
def test_clean_sql_strips_fences():
    assert p.clean_sql("```sql\nSELECT 1\n```") == "SELECT 1"


def test_clean_sql_null_like_returns_none():
    assert p.clean_sql("null") is None
    assert p.clean_sql("none") is None
    assert p.clean_sql(None) is None
    assert p.clean_sql(123) is None  # non-string


def test_clean_sql_keeps_first_statement():
    assert p.clean_sql("SELECT 1; DROP TABLE x") == "SELECT 1"


# ---- parse_decision ---------------------------------------------------------
def test_parse_decision_with_sql():
    text = json.dumps({"intent": "NEW_QUERY", "needs_sql": True, "sql": "SELECT 1"})
    dec = p.parse_decision(text)
    assert dec.parse_ok and dec.needs_sql and dec.sql == "SELECT 1"


def test_parse_decision_needs_sql_but_no_sql_is_reconciled():
    text = json.dumps({"intent": "NEW_QUERY", "needs_sql": True, "sql": None})
    dec = p.parse_decision(text)
    assert dec.needs_sql is False   # claimed but absent -> not a SQL turn


def test_parse_decision_no_json_degrades():
    dec = p.parse_decision("the model said something unparseable")
    assert dec.parse_ok is False
    assert dec.intent == p.INSUFFICIENT_CONTEXT
    assert dec.answer  # a friendly default answer, never empty


def test_parse_decision_bad_memory_update_survives():
    text = json.dumps({"needs_sql": False, "answer": "ok", "memory_update": "not-an-object"})
    dec = p.parse_decision(text)
    assert dec.answer == "ok"
    assert dec.memory_update.selected_tables == []  # bad field -> empty, never raises
