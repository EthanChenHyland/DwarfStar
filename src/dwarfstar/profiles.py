"""One profile matrix, shared by launcher and Workstation. No runtime settings here."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
def registry():return json.loads((ROOT/'acp/profiles.json').read_text())
def profile(kind):
    value=registry()['profiles'][kind]
    return {**value,'id':kind}

BASE_AGENT_CONFIG='memory:\n  backend: off\nautolearn:\n  enabled: false\ntools:\n  async: false\ntask:\n  maxConcurrency: 1\n  maxRecursionDepth: 1\n'
def agent_config(kind):
    """Keep native defaults unless a profile explicitly overrides async compaction."""
    overrides=profile(kind).get('omp_settings',{})
    if not isinstance(overrides,dict) or set(overrides)-{'compaction.asyncEnabled'}:
        raise ValueError('Unsupported profile OMP setting')
    if not overrides:return BASE_AGENT_CONFIG
    enabled=overrides['compaction.asyncEnabled']
    if not isinstance(enabled,bool):raise ValueError('Async compaction must be boolean')
    return BASE_AGENT_CONFIG+'compaction:\n  asyncEnabled: '+str(enabled).lower()+'\n'
