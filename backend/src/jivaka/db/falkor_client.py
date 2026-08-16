from functools import lru_cache

from falkordb import FalkorDB, Graph

from jivaka.config import get_settings


@lru_cache
def get_db() -> FalkorDB:
    settings = get_settings()
    return FalkorDB(host=settings.falkor_host, port=settings.falkor_port)


def get_graph() -> Graph:
    settings = get_settings()
    return get_db().select_graph(settings.falkor_graph_name)
