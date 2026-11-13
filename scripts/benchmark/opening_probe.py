import sys, json, collections

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "validate"))
from api_client import chat

port = int(sys.argv[1])
body = json.load(open(sys.argv[2]))
n = int(sys.argv[3])
maxtok = int(sys.argv[4]) if len(sys.argv) > 4 else 24
body["max_completion_tokens"] = maxtok
body.pop("max_tokens", None)
heads = collections.Counter()
rows = []
for i in range(n):
    r = chat(port, body, timeout=600)
    head = (r["reasoning_head"] or r["content"])[:60].replace("\n", "\\n")
    heads[head[:14]] += 1
    rows.append(
        {"i": i, "finish": r["finish_reason"], "head": head, "tools": r["tool_calls"], "out": (r["usage"] or {}).get("completion_tokens")}
    )
for h, c in heads.most_common(40):
    print(c, repr(h))
early = [r for r in rows if r["finish"] == "stop" and (r["out"] or 0) < maxtok]
print("trials", n, "early-stop", len(early))
for r in early[:10]:
    print("  ", r)
json.dump(rows, open(sys.argv[2] + ".heads.json", "w"))
