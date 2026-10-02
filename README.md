<p align="center">
  <img src="assets/xvimo_logo_animated.gif" alt="Xvimo — scam check before you pay" width="760">
</p>

<p align="center">
  <b>An agentic scam and fraud checker for Africa, built on NVIDIA Nemotron.</b><br>
  Forward a suspicious message. Get a clear verdict, the reasons, and the evidence — before you send money.
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-39FF88">
  <img alt="Models: NVIDIA Nemotron" src="https://img.shields.io/badge/models-NVIDIA%20Nemotron-00E5FF">
  <img alt="Search: Tavily" src="https://img.shields.io/badge/search-Tavily-00E5FF">
  <img alt="Status: in development" src="https://img.shields.io/badge/status-milestone%201-0B2545">
</p>

---

## Milestone 1 — Nano triage layer complete

**Date:** October 2026 · **Hackathon:** Nebius x NVIDIA Global AI Hackathon · **Track:** Best Apps and Agents

This milestone delivers Xvimo's first working layer: a fast triage step that reads any message and decides whether it is safe, a likely scam, or needs a deeper investigation. On our evaluation sample it caught **every scam** and **never gave a wrong answer when deciding alone**.

| Metric (50-message balanced sample) | Result |
|---|---|
| Overall accuracy | **98%** |
| Scams caught (recall) | **100%** (25 / 25) |
| False alarms on legitimate messages | **4%** (1 / 25)* |
| Messages settled by Nano alone | **52%** — with **100%** accuracy |
| Messages sent for deeper investigation | 48% |
| Valid structured output (JSON) | 100% |
| Average latency per check | ~5–8 s |

\* The single false alarm was routed to the investigation layer rather than returned as a final verdict.

> **Honesty note:** the prompt was tuned on this 50-message sample, so these numbers are optimistic. The full 300-message benchmark on unseen data will be run once the complete pipeline is finished, and reported here unchanged.

---

## The problem

Ponzi schemes, fake investment platforms, fake job interviews, loan-app fraud and bank impersonation cost ordinary people in Nigeria and across Africa their savings every year. The people most at risk are not reading regulator circulars or searching company registries — they live on WhatsApp and SMS, and they have seconds to decide whether to pay.

**Xvimo meets them there.** A user forwards the message; Xvimo replies with:

- a clear verdict — **Likely scam**, **Suspicious**, or **No red flags found**
- the specific reasons, in plain language
- cited evidence from the web (regulator warnings, news reports, registration checks)
- the answer in the user's language — English, Pidgin, Yoruba, Hausa or Igbo

---

## How it works

```mermaid
flowchart LR
    U[User forwards<br/>message or screenshot] --> N[Nemotron 3 Nano Omni<br/>triage]
    N -->|confident and low-risk| F[Final verdict<br/>from Nano]
    N -->|suspicious, uncertain,<br/>or high-risk topic| A[Nemotron 3 Ultra<br/>investigator agent]
    A <-->|chooses its own searches| T[Tavily web search]
    A --> V[Final verdict<br/>with cited evidence]
    F --> R[Reply to user]
    V --> R
```

### Layer 1 — Triage (✅ this milestone)

**Model:** `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`

Nano reads each message and returns structured JSON: verdict, confidence, scam type, language, organisations, links, promised returns, red flags, and whether a web check is needed. The Omni model also reads images, so users will be able to forward **screenshots** of scam messages.

### Routing — decided in code, not by the model (✅ this milestone)

A missed scam is far more harmful than a false alarm, so routing is asymmetric:

- Nano **may raise an alarm on its own** when it is confident.
- Nano **may only give the all-clear on its own** when it is confident **and** the message avoids every high-risk topic (job interviews, BVN, ATM or account blocks, investments, loans, prizes, links, codes, fees).
- Everything else goes to the investigator.

*The cheap model can raise an alarm alone, but it can never clear a risky message without a second opinion.*

### Layer 2 — Investigator agent (🔜 next milestone)

**Model:** `nvidia/nemotron-3-ultra-550b-a55b` · **Tool:** Tavily search

Ultra receives escalated cases with tools. It decides what needs verifying, writes its own search queries, reads the results, searches again if the evidence is thin, and returns a final verdict with cited sources.

---

## Scam types covered

| Type | What it looks like |
|---|---|
| `ponzi` | Invest or deposit to receive guaranteed, high or fast returns |
| `advance_fee` | Pay a fee to *receive* promised money — grants, packages, visas, scholarships |
| `fake_loan` | Pay a fee to get a loan, or threats about an unpaid loan |
| `fake_job` | Unsolicited interview or "recruitment test" invitations from unknown firms |
| `impersonation` | Fake bank, CBN or EFCC messages; "new number" relatives; "I sent money by mistake" |
| `phishing_link` | Links asking for login, card, BVN, NIN or OTP details |
| `fake_giveaway` | "You won" a prize and must pay or share details to claim it |
| `romance` | Online relationships that turn into requests for money or gift fees |

---

## Evaluation dataset

A 300-message labelled benchmark (169 scam / 131 legitimate) covering 8 scam types, 6 channels and 5 languages.

| Source | Scam | Legit |
|---|---|---|
| Nigerian SMS spam (Data Science Nigeria, via a public research repository) — real | 120 | — |
| UCI SMS Spam Collection — real, non-Nigerian | — | 45 |
| Constructed from documented Nigerian message patterns, clearly labelled | 49 | 86 |

All personal data is redacted (`[NAME]`, `[PHONE]`, `[ACCOUNT]`, `[EMAIL]`, `[AMOUNT]`). Results are reported **separately for real and constructed rows**, and constructed rows are being replaced with real messages over time.

The dataset is **not published in this repository** because part of it comes from a source without a redistribution licence. Evaluation scripts and aggregate results are public.

---

## Getting started

### Requirements

- Python 3.10+
- An NVIDIA API key from [build.nvidia.com](https://build.nvidia.com) (or a Nebius Token Factory key — see below)
- A Tavily API key from [tavily.com](https://tavily.com)

### Setup

```bash
git clone https://github.com/DataGuy-Eterniti/xvimo.git
cd xvimo
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_API_KEY=your_nvidia_api_key
NANO_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
ULTRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b
TAVILY_API_KEY=your_tavily_api_key
```

`.env` is listed in `.gitignore` — never commit your keys.

### Run

```bash
# Check that both models and Tavily are reachable
python test_setup.py

# Triage a single example message
python triage.py

# Evaluate triage on a balanced sample (N scam + N legit) from data/xvimo_dataset.xlsx
python run_triage_sample.py 25
```

Results are written to `results/triage_sample.csv`.

---

## Platform: NVIDIA today, Nebius Token Factory for the final build

Xvimo is provider-independent: every endpoint, key and model name lives in `.env`, and the code uses the OpenAI-compatible API. During development we use NVIDIA's hosted API for the same Nemotron models. The final submission runs on **Nebius Token Factory** by changing four lines in `.env`:

```env
LLM_BASE_URL=https://api.tokenfactory.nebius.com/v1/
LLM_API_KEY=your_nebius_api_key
NANO_MODEL=<Nemotron Nano model ID on Token Factory>
ULTRA_MODEL=<Nemotron Ultra model ID on Token Factory>
```

No code changes are required.

---

## Project structure

```
xvimo/
├── assets/                  # logo and media
├── data/                    # evaluation dataset (private, git-ignored)
├── results/                 # evaluation outputs (git-ignored)
├── triage.py                # Layer 1: Nemotron Nano triage + routing
├── run_triage_sample.py     # balanced-sample evaluation
├── test_setup.py            # connectivity check for models and Tavily
├── requirements.txt
├── .env                     # your keys (git-ignored)
└── LICENSE                  # MIT
```

---

## Roadmap

- [x] Project setup, model and Tavily connectivity
- [x] 300-message labelled, redacted evaluation dataset
- [x] **Milestone 1:** Nano triage with structured output, safety-net routing, 100% recall on sample
- [ ] **Milestone 2:** Ultra investigator agent with Tavily tool calling and cited verdicts
- [ ] Multilingual replies (English, Pidgin, Yoruba, Hausa, Igbo)
- [ ] WhatsApp integration and screenshot checks
- [ ] Deployment on Nebius Serverless Endpoints
- [ ] Full 300-message benchmark: accuracy, false-positive rate, cost and latency per model tier
- [ ] Routing dashboard

---

## Development log

**Milestone 1 — how we got to 100% recall**

| Iteration | Change | Recall | False alarms |
|---|---|---|---|
| 1 | First prompt | 100% | 28% |
| 2 | Core rule: only *requests for risky actions* or *unrealistic promises* count as red flags; known legitimate patterns (OTPs, alerts, telco notices) | 88% | 4% |
| 3 | Redacted details still count as actions; unsolicited job invites flagged; code-based safety net for high-risk topics | **100%** | **4%** |

The key lesson: fixing false alarms in the prompt made the model too lenient on real scams. Moving the final "is it safe?" decision for risky topics out of the model and into code solved both problems at once.

---

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

Built for the Nebius x NVIDIA Global AI Hackathon with NVIDIA Nemotron open models and Tavily search. Nigerian SMS samples originate from Data Science Nigeria research data; legitimate SMS samples from the UCI SMS Spam Collection.