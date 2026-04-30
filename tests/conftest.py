from __future__ import annotations

import os


# Force deterministic classifier path for test stability regardless caller shell env.
os.environ["LLM_ENABLE_LIVE_CALLS"] = "false"
