from abc import ABC, abstractmethod

from jivaka.ingestion.models import Chunk, ChunkExtraction


class EntityExtractor(ABC):
    @abstractmethod
    def extract(self, chunk: Chunk) -> ChunkExtraction:
        ...
