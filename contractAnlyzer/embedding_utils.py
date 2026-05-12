import os
import numpy as np
import pymongo
import faiss
from sentence_transformers import SentenceTransformer
from config import *

def load_and_chunk_text(folder):
    chunks = []
    for fname in os.listdir(folder):
        if fname.endswith(".txt"):
            with open(os.path.join(folder, fname), encoding="utf-8") as f:
                text = f.read()
                tokens = text.split()
                for i in range(0, len(tokens), CHUNK_SIZE - CHUNK_OVERLAP):
                    chunk = " ".join(tokens[i:i+CHUNK_SIZE])
                    if chunk:
                        chunks.append({"chunk": chunk, "file": fname})
    return chunks

def store_embeddings_to_mongo(chunks):
    model = SentenceTransformer(EMBED_MODEL_NAME)
    embeddings = model.encode([c["chunk"] for c in chunks], convert_to_numpy=True)

    client = pymongo.MongoClient(MONGO_URI)
    collection = client[DB_NAME][COLLECTION_NAME]

    for i, c in enumerate(chunks):
        doc = {
            "chunk": c["chunk"],
            "file": c["file"],
            "embedding": embeddings[i].tolist()
        }
        collection.insert_one(doc)

def load_embeddings_from_mongo():
    client = pymongo.MongoClient(MONGO_URI)
    collection = client[DB_NAME][COLLECTION_NAME]
    documents = list(collection.find({}))
    chunks = [doc["chunk"] for doc in documents]
    embeddings = np.array([doc["embedding"] for doc in documents])
    return chunks, embeddings

def build_faiss_index(embeddings):
    index = faiss.IndexFlatL2(embeddings.shape[1])
    index.add(embeddings)
    return index