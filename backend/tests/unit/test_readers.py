import json

import pytest

from jivaka.ingestion.chunking.base import group_by_max_chars, split_paragraphs
from jivaka.ingestion.intake import SUPPORTED_EXTENSIONS, load_document
from jivaka.ingestion.readers import (
    FileReadError,
    delimited_to_text,
    html_to_text,
    json_lines_to_text,
    json_to_text,
    markdown_to_text,
    xml_to_text,
)


def test_html_drops_markup_scripts_and_navigation_and_keeps_paragraphs():
    html = b"""<html><head><title>Asthma</title><style>p{color:red}</style></head>
    <body><nav><a href="/">Home</a></nav>
    <h1>Asthma</h1><p>Asthma is a <b>chronic</b> disease of the airways.</p>
    <script>alert(1)</script>
    <ul><li>Wheezing</li><li>Cough</li></ul>
    <table><tr><th>Drug</th><th>Dose</th></tr><tr><td>Salbutamol</td><td>100 mcg</td></tr></table>
    <footer>Copyright</footer></body></html>"""

    text = html_to_text(html)

    assert "Asthma is a chronic disease of the airways." in text
    assert "Wheezing" in split_paragraphs(text)
    assert "Salbutamol | 100 mcg" in text
    for junk in ("alert", "color:red", "Home", "Copyright", "<"):
        assert junk not in text


def test_xml_flattens_records_and_keeps_mixed_content_prose():
    xml = b"""<?xml version="1.0"?>
    <DescriptorRecordSet>
      <DescriptorRecord>
        <DescriptorUI>D001249</DescriptorUI>
        <DescriptorName><String>Asthma</String></DescriptorName>
        <ScopeNote>A form of bronchial disorder with three distinct components.</ScopeNote>
      </DescriptorRecord>
    </DescriptorRecordSet>"""

    paragraphs = split_paragraphs(xml_to_text(xml))

    assert "DescriptorUI: D001249" in paragraphs[0]
    assert "ScopeNote: A form of bronchial disorder with three distinct components." in paragraphs[0]
    assert "String: Asthma" in paragraphs

    jats = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec>
      <title>Introduction</title>
      <p>Metformin is a <italic>first-line</italic> drug for type 2 diabetes.</p>
    </sec></body></article>"""
    assert split_paragraphs(xml_to_text(jats)) == [
        "Introduction",
        "Metformin is a first-line drug for type 2 diabetes.",
    ]


def test_xml_does_not_expand_external_entities(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    xml = f"""<?xml version="1.0"?>
    <!DOCTYPE note [<!ENTITY xxe SYSTEM "file://{secret}">]>
    <note><body>&xxe;</body></note>""".encode()

    try:
        text = xml_to_text(xml)
    except FileReadError:
        return
    assert "TOP SECRET" not in text


def test_bad_xml_and_json_raise_readable_errors():
    with pytest.raises(FileReadError):
        xml_to_text(b"<a><b></a>")
    with pytest.raises(FileReadError):
        json_to_text(b"{not json")


def test_json_records_become_field_value_paragraphs():
    data = {
        "drug": "Metformin",
        "class": "Biguanide",
        "indications": ["type 2 diabetes", "PCOS"],
        "description": "Metformin lowers hepatic glucose production. " * 6,
        "interactions": [
            {"with": "Contrast media", "risk": "lactic acidosis"},
            {"with": "Alcohol", "risk": "lactic acidosis"},
        ],
        "approved": True,
        "notes": None,
    }

    paragraphs = split_paragraphs(json_to_text(json.dumps(data).encode()))

    assert paragraphs[0] == (
        "drug: Metformin\nclass: Biguanide\nindications: type 2 diabetes, PCOS\napproved: yes"
    )
    assert paragraphs[1].startswith("description:\nMetformin lowers hepatic glucose production.")
    assert "interactions\nwith: Contrast media\nrisk: lactic acidosis" in paragraphs


def test_json_lines_reads_one_record_per_line():
    raw = b'{"term": "Asthma"}\n\n{"term": "Gout"}\n'
    assert split_paragraphs(json_lines_to_text(raw)) == ["term: Asthma", "term: Gout"]


def test_markdown_strips_syntax_and_keeps_text():
    md = """---
title: Notes
---
# Hypertension

Blood pressure **above 140/90** is *high*. See [guidelines](https://example.org).

- Lifestyle change
- ACE inhibitors such as `enalapril`

| Drug | Dose |
|------|------|
| Enalapril | 5 mg |

![chart](chart.png)
"""
    paragraphs = split_paragraphs(markdown_to_text(md))

    assert paragraphs[0] == "Hypertension"
    assert paragraphs[1] == "Blood pressure above 140/90 is high. See guidelines."
    assert "Lifestyle change\nACE inhibitors such as enalapril" in paragraphs
    assert "Drug | Dose\nEnalapril | 5 mg" in paragraphs
    assert "title: Notes" not in markdown_to_text(md)


def test_csv_and_tsv_rows_become_paragraphs():
    csv_text = "code,name,note\nE11,Type 2 diabetes,\nJ45,Asthma,\"chronic, airways\"\n"
    assert split_paragraphs(delimited_to_text(csv_text, delimiter=None)) == [
        "code: E11\nname: Type 2 diabetes",
        "code: J45\nname: Asthma\nnote: chronic, airways",
    ]
    tsv_text = "code\tname\nI10\tEssential hypertension\n"
    assert delimited_to_text(tsv_text, delimiter="\t") == "code: I10\nname: Essential hypertension"


@pytest.mark.parametrize(
    "name, content, expected",
    [
        ("page.html", b"<p>Gout is <em>inflammatory</em>.</p>", "Gout is inflammatory."),
        ("note.md", "## โรคหืด\n\nผู้ป่วย **หอบ**".encode(), "โรคหืด\n\nผู้ป่วย หอบ"),
        ("data.json", b'{"a": "b"}', "a: b"),
        ("rows.tsv", b"x\ty\n1\t2\n", "x: 1\ny: 2"),
        ("bom.txt", "﻿hello".encode("utf-8"), "hello"),
    ],
)
def test_load_document_reads_new_formats(tmp_path, name, content, expected):
    path = tmp_path / name
    path.write_bytes(content)

    loaded = load_document(path)

    assert [page.text for page in loaded.pages] == [expected]
    assert loaded.pages[0].image_bytes is None


def test_supported_extensions_include_new_formats():
    for suffix in (".html", ".htm", ".xml", ".json", ".jsonl", ".md", ".csv", ".tsv"):
        assert suffix in SUPPORTED_EXTENSIONS


def test_long_record_without_full_stops_is_split_on_lines():
    record = "\n".join(f"field{i}: value {i}" for i in range(200))
    blocks = group_by_max_chars([record], max_chars=300)

    assert len(blocks) > 1
    assert all(len(block) <= 300 for block in blocks)
    assert blocks[0].startswith("field0: value 0\nfield1: value 1")
