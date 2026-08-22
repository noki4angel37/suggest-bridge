from __future__ import annotations

import sys
from pathlib import Path

# Allow `import bot.core...` when pytest is started from the repo root.
PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
