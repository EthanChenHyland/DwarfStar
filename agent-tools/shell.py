"""Bounded shell inside the inherited OS sandbox; logs installs without banning them."""
from pathlib import Path
import ctypes, json, os, re, signal, subprocess, sys, time

TOOLS=Path(__file__).resolve().parent
sys.path.insert(0,str(TOOLS.parents[2]/'v7/tools'))
from common import redact, secret_values

from permissions import decision
sys.path.insert(0,str(TOOLS.parent))
from approval_policy import mode,record

def restricted(command):
    # User confirmation does not weaken hard OS/global/credential boundaries.
    return decision(command,os.environ['DWARFSTAR_WORKSPACE'])['sandbox_blocked']

def main():
    p=json.load(sys.stdin); workspace=Path(os.environ['DWARFSTAR_WORKSPACE']).resolve()
    if Path.cwd().resolve()!=workspace: raise ValueError('Workspace mismatch')
    lib=ctypes.CDLL('/usr/lib/libsandbox.1.dylib')
    if os.environ.get('DWARFSTAR_TOOLING_ACTIVE')!='1' or (mode()!='full_auto' and lib.sandbox_check(os.getpid(),b'appleevent-send',0)!=1):
        raise ValueError('Development shell requires inherited macOS sandbox')
    command=p['command']; started=time.monotonic();stamp=time.time_ns()
    logs=workspace/'.dwarfstar-tooling-logs';logs.mkdir(exist_ok=True)
    values=secret_values(workspace)
    receipt={'at':time.time(),'command':redact(command,values),'workspace':str(workspace),'tier':'2' if re.search(r'\b(?:npm|npx|pnpm|bunx?|pip|uv|curl|wget|git\s+clone)\b',command) else '1'}
    code=126;out='Approval required: request an explicit operator action for global/system/credential changes. The sandbox does not self-escalate.'
    if not restricted(command):
        child=subprocess.Popen(['/bin/zsh','-f','-c',command],cwd=workspace,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        def stop(*_):
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:
            out,_=child.communicate(timeout=max(1,min(float(p.get('timeout',120)),1800)));code=child.returncode
        except subprocess.TimeoutExpired:
            stop()
            try:out,_=child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);out,_=child.communicate()
            code=124;out+='\nCommand timed out; owned process group stopped.'
        finally:
            # Background servers belong in a bounded same-call validation workflow.
            stop()
            time.sleep(.1)
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
    if code and re.search(r'sudo:.*(?:password|terminal)|User interaction is not allowed|not authorized to send Apple events|requires administrator privileges',out,re.I):
        record({'event':'external_os_authorization','status':'External OS authorization required'})
        out+='\nExternal OS authorization required; ACP approval does not grant OS authorization.'
    out=redact(out,values); path=logs/(str(stamp)+'.log');path.write_text(out)
    receipt.update(exit_code=code,elapsed_s=time.monotonic()-started,log=str(path))
    with (logs/'actions.jsonl').open('a') as f:f.write(json.dumps(receipt)+'\n')
    print(json.dumps({'exit_code':code,'output':out[-18000:],'log':str(path),'tier':receipt['tier'],'elapsed_s':receipt['elapsed_s']}))

if __name__=='__main__':
    try:main()
    except Exception as e: print(json.dumps({'exit_code':126,'output':str(e)}));sys.exit(1)
