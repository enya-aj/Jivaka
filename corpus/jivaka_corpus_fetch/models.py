from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["vocabulary", "corpus", "patient-record"]
LicenseClass = Literal["green", "amber", "red"]

# Kept in sync with corpus/manifest.tsv's status column.
ManifestStatus = Literal[
    "OK", "FAIL", "FAIL-HTML", "FAIL-SIZE", "FAIL-TYPE", "SKIP-GATED", "SKIP-BLOCKED", "SKIP-DEAD"
]

MANIFEST_COLUMNS = [
    "status",
    "bytes",
    "sha256_12",
    "path",
    "url",
    "category",
    "license_class",
    "name",
    "fetched_at",
    "http_status",
    "content_type",
    "notes",
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceEntry(BaseModel):
    name: str
    category: Category
    license_class: LicenseClass
    url: str
    dest: Optional[str] = None
    expected_content_type: list[str] = Field(default_factory=list)
    expected_extension: Optional[str] = None
    min_bytes: int = 0
    fetchable: bool = True
    skip_reason: Optional[str] = None
    notes: Optional[str] = None

    def model_post_init(self, __context) -> None:
        if not self.fetchable and not self.skip_reason:
            raise ValueError(f"{self.name}: fetchable=false requires a skip_reason")
        if self.fetchable and not self.dest:
            raise ValueError(f"{self.name}: fetchable=true requires a dest path")


class ManifestRow(BaseModel):
    status: ManifestStatus
    bytes: int = 0
    sha256_12: str = "-"
    path: str = "-"
    url: str
    category: Category
    license_class: LicenseClass
    name: str
    fetched_at: str = Field(default_factory=utcnow_iso)
    http_status: str = "-"
    content_type: str = "-"
    notes: str = ""

    def to_tsv_row(self) -> str:
        values = [str(getattr(self, col)).replace("\t", " ").replace("\n", " ") for col in MANIFEST_COLUMNS]
        return "\t".join(values)

    @classmethod
    def from_tsv_row(cls, line: str) -> "ManifestRow":
        values = line.rstrip("\n").split("\t")
        data = dict(zip(MANIFEST_COLUMNS, values))
        data["bytes"] = int(data.get("bytes", 0) or 0)
        return cls(**data)
