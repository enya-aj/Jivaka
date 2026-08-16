from typing import Optional

from jivaka.ingestion.linking.base import DefinitionSource
from jivaka.ingestion.models import Definition


class UMLSDefinitionSource(DefinitionSource):
    """Not implemented. Real MeSH/UMLS lookups require a licensed UMLS
    Metathesaurus dataset or a UTS API key, neither of which has been
    provisioned yet (see the plan's deferred-decisions list). Keep
    DEFINITION_SOURCE=stub until this is implemented."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "UMLS/MeSH definition lookup requires a UTS API key/license that "
            "has not been configured yet. Set DEFINITION_SOURCE=stub for now."
        )

    def lookup(self, term: str, entity_type: str) -> Optional[Definition]:
        raise NotImplementedError
