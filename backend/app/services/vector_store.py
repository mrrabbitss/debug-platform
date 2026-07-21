from functools import lru_cache
import uuid
from typing import Iterable

from sklearn.feature_extraction.text import HashingVectorizer

from app.core.config import get_settings


VECTOR_SIZE = 384
_vectorizer = HashingVectorizer(
    n_features=VECTOR_SIZE,
    analyzer="char_wb",
    ngram_range=(3, 5),
    alternate_sign=False,
    norm="l2",
)


def vectorize(texts: list[str]) -> list[list[float]]:
    return _vectorizer.transform(texts).toarray().astype(float).tolist()


class OptionalQdrantStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.enabled = bool(self.settings.qdrant_url)
        self.client = None
        if self.enabled:
            from qdrant_client import QdrantClient

            self.client = QdrantClient(url=self.settings.qdrant_url, api_key=self.settings.qdrant_api_key or None)
            self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client:
            return
        from qdrant_client.models import Distance, VectorParams

        name = self.settings.qdrant_collection
        try:
            exists = self.client.collection_exists(name)
        except AttributeError:
            exists = any(item.name == name for item in self.client.get_collections().collections)
        if not exists:
            self.client.create_collection(name, vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE))

    def upsert(self, rows: Iterable[dict]) -> None:
        if not self.client:
            return
        from qdrant_client.models import PointStruct

        items = list(rows)
        if not items:
            return
        vectors = vectorize([item["text"] for item in items])
        points = [
            PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, item["id"])), vector=vector, payload={**item.get("payload", {}), "chunk_id": item["id"]})
            for item, vector in zip(items, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.settings.qdrant_collection, points=points, wait=True)

    def delete_document(self, document_id: str) -> None:
        if not self.client:
            return
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        self.client.delete(
            collection_name=self.settings.qdrant_collection,
            points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]),
            wait=True,
        )

    def search(self, query: str, limit: int = 20) -> dict[str, float]:
        if not self.client:
            return {}
        vector = vectorize([query])[0]
        try:
            response = self.client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=vector,
                limit=limit,
                with_payload=True,
            )
            points = response.points
        except AttributeError:
            points = self.client.search(
                collection_name=self.settings.qdrant_collection,
                query_vector=vector,
                limit=limit,
                with_payload=True,
            )
        return {str((point.payload or {}).get("chunk_id", point.id)): float(point.score) for point in points}


@lru_cache

def get_vector_store() -> OptionalQdrantStore:
    return OptionalQdrantStore()
