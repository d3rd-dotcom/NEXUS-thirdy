# FIXED (C1): This file is intentionally empty.
# The previous content duplicated the Settings class, creating two separate
# instances and silently breaking has_cerebras(), has_fetchai(), and Phase 9
# features for any code that imported via `from config import settings`
# instead of `from config.settings import settings`.
# All settings live exclusively in config/settings.py.
