from jivaka.retrieval.terms import extract_terms, find_pseudonyms, has_thai


def test_english_terms_are_ngrams_without_stopword_edges():
    terms = extract_terms("What are the first-line treatments for type 2 diabetes?")

    assert "type 2 diabetes" in terms
    assert "diabetes" in terms
    assert "first-line treatments" in terms
    assert "the" not in terms
    assert "for type 2" not in terms


def test_thai_terms_are_also_joined_without_spaces():
    terms = extract_terms("อาการของโรคไข้เลือดออก")

    assert any("ไข้เลือดออก" in t for t in terms)
    assert all(" " not in t or has_thai(t) for t in terms)


def test_pseudonyms_are_found_and_not_used_as_terms():
    question = "What medications has p-0042 been given?"

    assert find_pseudonyms(question) == ["P-0042"]
    assert not any("0042" in t for t in extract_terms(question))
