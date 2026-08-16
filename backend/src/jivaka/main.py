from fastapi import FastAPI

from jivaka.api.routes import router

app = FastAPI(title="Jivaka Ingestion API")
app.include_router(router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
