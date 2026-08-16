from jivaka.ingestion.linking.stub_dictionary import StubDictionarySource


def test_lookup_known_term_returns_definition():
    source = StubDictionarySource()

    definition = source.lookup("Hypertension", "diagnosis")

    assert definition is not None
    assert definition.term == "hypertension"
    assert "blood pressure" in definition.definition_text.lower()


def test_lookup_is_case_and_whitespace_insensitive():
    source = StubDictionarySource()

    assert source.lookup("  HYPERTENSION  ", "diagnosis") is not None


def test_lookup_unknown_term_returns_none():
    source = StubDictionarySource()

    assert source.lookup("some nonexistent clinical term xyz", "other") is None
