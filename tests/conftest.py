"""Keep the automated suite independent from a developer's local model settings."""

import os


# config.py loads .env at import time. Defining these keys before test-module
# collection prevents a real DeepSeek call or BGE model load from making local
# regression tests slow and nondeterministic.
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_PROVIDER"] = "feature_hash"
os.environ["EMBEDDING_WARMUP_ENABLED"] = "false"
