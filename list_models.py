"""List the NVIDIA / Nemotron models available on the endpoint in .env (e.g. Nebius Token Factory).

Usage:  python list_models.py
Copy the exact IDs it prints into NANO_MODEL and ULTRA_MODEL in .env.
"""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)
client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))

print("Endpoint:", os.getenv("LLM_BASE_URL"))
models = sorted(m.id for m in client.models.list().data)
nvidia = [m for m in models if "nemotron" in m.lower() or m.lower().startswith("nvidia/")]

print(f"\n{len(models)} models available. NVIDIA / Nemotron models:\n")
for m in nvidia or ["(none found — check that LLM_BASE_URL and LLM_API_KEY are the Nebius ones)"]:
    tag = ""
    low = m.lower()
    if "nano" in low:
        tag = "  <- candidate for NANO_MODEL" + (" (reads images)" if ("omni" in low or "vl" in low) else "")
    elif "ultra" in low:
        tag = "  <- candidate for ULTRA_MODEL"
    elif "super" in low:
        tag = "  <- fallback for ULTRA_MODEL if Ultra is missing"
    print("  ", m + tag)
