from jivaka.ingestion.linking.base import DefinitionSource
from jivaka.ingestion.linking.stub_dictionary import StubDictionarySource
from jivaka.ingestion.linking.umls_source import UMLSDefinitionSource

_SOURCES: dict[str, type[DefinitionSource]] = {
    "stub": StubDictionarySource,
    "umls": UMLSDefinitionSource,
}


def get_definition_source(name: str) -> DefinitionSource:
    try:
        return _SOURCES[name]()
    except KeyError as exc:
        raise ValueError(
            f"Unknown DEFINITION_SOURCE={name!r}. Options: {sorted(_SOURCES)}"
        ) from exc
