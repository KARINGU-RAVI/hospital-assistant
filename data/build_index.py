"""Chunks data/faq/*.md, embeds with FastEmbed, stores in data/chroma/ (rebuilt from scratch).

Run: python data/build_index.py   (first run downloads the ~67 MB model into data/models/)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow `python data/build_index.py`

from agent.config import get_settings  # noqa: E402
from agent.tools.rag import build_index  # noqa: E402

if __name__ == "__main__":
    s = get_settings()
    count = build_index(s.faq_path, s.chroma_path, s.models_path)
    print(f"Indexed {count} chunks from {s.faq_path} into {s.chroma_path}")
