// Read-only reuse of the installed passive snapshot; no ACP/provider is started.
import {configSnapshot} from '../acp/benchmark-snapshot.ts';
import {infrastructureSettings,activeProfile} from '../acp/lifecycle.ts';
console.log(JSON.stringify({profile_id:activeProfile().id,...configSnapshot(infrastructureSettings())}));
