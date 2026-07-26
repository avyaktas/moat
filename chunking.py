"""Split filing text into overlapping chunks for embedding. """

def chunk_text(text: str, size: int = 800, overlap: int = 150) -> list[str]:
    """Split text into overlapping chunks of roughly `size` characters.

    Chunks step forward by (size - overlap), so consecutive chunks share
    `overlap` characters. Whitespace-only chunks are dropped.
    """
    if not text:
        return []

    chunks = []
    step = size - overlap
    for start in range(0, len(text), step):
        chunk = text[start:start + size].strip()
        if chunk:
            chunks.append(chunk)
    return chunks