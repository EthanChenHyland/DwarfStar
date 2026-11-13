
import os as _os

# Hermetic: the machine's config/local.json runtime_overrides (for example Splash selected locally) never apply here.
_os.environ["DWARFSTAR_IGNORE_LOCAL_OVERRIDES"] = "1"
