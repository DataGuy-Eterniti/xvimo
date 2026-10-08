"""Where Xvimo keeps runtime data. On Nebius Serverless, mount a volume and set XVIMO_DATA_DIR to it so the
event log and feedback survive restarts; locally it defaults to ./data and ./results."""
import os

DATA_DIR = os.getenv("XVIMO_DATA_DIR", "data")
RESULTS_DIR = os.getenv("XVIMO_RESULTS_DIR", "results")
EVENTS_PATH = os.path.join(DATA_DIR, "events.jsonl")
FEEDBACK_PATH = os.path.join(DATA_DIR, "feedback.jsonl")
VISITS_PATH = os.path.join(DATA_DIR, "visits.jsonl")
