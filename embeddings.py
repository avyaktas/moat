""" Local sentence embeddings for semantic search over filings.

Uses all-MiniLM-L6-v2 via sentence-transformers: 384 dimensiojns, 
~90MB, runs on CPU in ms. Local rather than API so theres no second
ket, per-querycost, or network dependancy at query time. 

Model cached a module level - loading takes a few seconds and downloads
~90MB on first use so tests that dont need embeddings never trigger it.
"""

from functools import lru_cache

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

@lru_cache(maxsize=1)
def get_model():
    """Load embedding model once per process"""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)

def embed(texts: list[str]) -> list[list[float]]:
    '''Embed a list of texts into 384-dim vectos'''
    model = get_model()
    return model.encode(texts, show_progress_bar=False).tolist()

def embed_one(text:str) -> list[float]:
    '''embed a single text'''
    return embed([text])[0]