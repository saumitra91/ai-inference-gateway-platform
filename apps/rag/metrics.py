from prometheus_client import Counter, Gauge, Histogram

rag_ingestion_duration = Histogram(
    "rag_ingestion_duration_seconds",
    "Time to ingest and index a document",
    buckets=[1, 2, 5, 10, 30, 60, 120, 300],
)
rag_embedding_latency = Histogram(
    "rag_embedding_latency_seconds",
    "Time to generate embeddings (per batch)",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
)
rag_retrieval_latency = Histogram(
    "rag_retrieval_latency_seconds",
    "Time to retrieve chunks from vector DB",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)
rag_vector_db_latency = Histogram(
    "rag_vector_db_latency_seconds",
    "Time for raw ChromaDB operations",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)
rag_retrieved_chunks = Histogram(
    "rag_retrieved_chunks_per_query",
    "Number of chunks retrieved per RAG query",
    buckets=[0, 1, 2, 3, 5, 8, 10, 15],
)
rag_completions_total = Counter(
    "rag_completions_total",
    "Total RAG-augmented chat completions",
)
rag_hallucination_fallbacks_total = Counter(
    "rag_hallucination_fallbacks_total",
    "Count of 'not found in documents' responses due to low confidence",
)
rag_documents_uploaded_total = Counter(
    "rag_documents_uploaded_total",
    "Total documents uploaded",
)
rag_chunks_stored_total = Counter(
    "rag_chunks_stored_total",
    "Total chunks stored in vector DB",
)
rag_documents_ready = Gauge(
    "rag_documents_ready",
    "Number of documents with status=ready",
)
