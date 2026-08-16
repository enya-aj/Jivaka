import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from jivaka.config import get_settings
from jivaka.db.falkor_client import get_graph
from jivaka.db.graph_reader import get_document_graph
from jivaka.ingestion.models import DocType, IngestionResult
from jivaka.ingestion.pipeline import ingest_document

router = APIRouter()


@router.post("/ingest", response_model=IngestionResult)
async def ingest_endpoint(
    file: UploadFile = File(...),
    doc_type: DocType = Form(...),
) -> IngestionResult:
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid4().hex}_{file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    graph = get_graph()
    try:
        return ingest_document(path=dest, doc_type=doc_type, graph=graph)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/documents/{doc_id}/graph")
async def get_graph_endpoint(doc_id: str) -> dict:
    graph = get_graph()
    dump = get_document_graph(graph, doc_id)
    if dump is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return dump
