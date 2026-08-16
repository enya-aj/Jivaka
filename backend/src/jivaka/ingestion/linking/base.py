from abc import ABC, abstractmethod
from typing import Optional

from jivaka.ingestion.models import Definition


class DefinitionSource(ABC):
    @abstractmethod
    def lookup(self, term: str, entity_type: str) -> Optional[Definition]:
        ...
