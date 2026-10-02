import os
from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient

load_dotenv()
llm = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
msg = "Invest N50,000 today and earn N200,000 weekly. Limited slots, pay now!"

for tier in ["NANO_MODEL", "ULTRA_MODEL"]:
    r = llm.chat.completions.create(model=os.getenv(tier),
        messages=[{"role": "user", "content": f"Is this a scam? Answer in 2 sentences: {msg}"}])
    print(f"\n[{tier}]", r.choices[0].message.content)

t = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
for x in t.search("SEC Nigeria warning unregistered investment scheme", max_results=3)["results"]:
    print("\n[TAVILY]", x["title"], "-", x["url"])