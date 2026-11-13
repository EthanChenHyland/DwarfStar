"""Tokenizer parity: the native runtime's token IDs against Splash's, for the same GGUF and the same text.

Native IDs come from the pinned runtime's own tokenizer (`ds4 --dump-tokens --raw`, built from the same objects as
the pinned ds4-server; it reads only GGUF metadata, no weights). Splash IDs come from Splash's production path:
`install.gguf.tokenizer_files` derives tokenizer.json from the GGUF metadata, `AutoTokenizer.from_pretrained` loads it
as the server does, and prompts are encoded with `tokenizable(text)` and `add_special_tokens=False`
(server/tokenization.py). Run it with Splash's own Python:

    <splash dist>/python/bin/python3 tokenizer_parity.py --gguf MAIN.gguf --native-cli ds4 --splash-src SRC
        [--reference DIR] [--json OUT]

--reference adds a third tokenizer: the model's own shipped tokenizer.json (for example Swift 1.5's source
checkpoint), loaded with the tokenizers library exactly as the file specifies it, so both runtimes are also checked
against the model's published tokenizer. (transformers 5's Qwen2Tokenizer class is not used: it replaces the file's
pre-tokenizer with its own Qwen2 pattern, which differs from Qwen3.5-family files on combining marks.)

Cases cover plain English, code, punctuation, whitespace, Unicode (combining marks, NFC/NFD, CJK, emoji, RTL),
JSON tool calls, special tokens, and prompts rendered from the GGUF's own chat template (system, multi-turn, tools,
tool results, thinking on/off with each reasoning effort). Exit status 0 only when every case matches exactly.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLAIN = [
    "Hello, world!",
    "The quick brown fox jumps over the lazy dog.",
    "I'm sure they'll say it's fine; we'd've known. YOU'RE IT'S I'LL WE'VE",
    "Dr. Smith's 3rd appointment is at 10:45 a.m. on 2026-11-13 (UTC-6).",
    "",
    " ",
    "a",
]
CODE = [
    "def add(a: int, b: int) -> int:\n    return a + b\n",
    "for (int i = 0; i < n; ++i) {\n\tsum += x[i] * y[i];\n}\n",
    "#include <stdio.h>\nint main(void){printf(\"%d\\n\", 42);return 0;}",
    "const f = async (url) => { const r = await fetch(url); return r.json(); };",
    "SELECT id, name FROM users WHERE created_at >= '2026-01-01' ORDER BY id DESC LIMIT 10;",
    "    if x:\n        pass\n\n\n    else:\n\t\tbreak  \n",
    "$ ls -la /tmp && grep -rn \"TODO\" src/ | wc -l",
    "x=1;y=2;z=x**y//3%4^5|6&7<<8>>9",
]
PUNCTUATION = [
    "!!!???...,,,;;;:::",
    "\"quoted\" 'single' `back` «guillemets» “curly” ‘curly’",
    "a--b---c—d–e…f",
    "(((nested))) [[[brackets]]] {{{braces}}} <<<angles>>>",
    "@#$%^&*_+=|\\/~",
]
WHITESPACE = [
    "  leading and trailing  ",
    "tabs\there\tand\tthere",
    "line1\nline2\r\nline3\rline4",
    "\n\n\nthree newlines",
    "trailing spaces then newline   \n",
    "mixed \t \n \t\n  end",
    "\u00a0non-breaking\u00a0space and\u2003em space and\u200bzero width",
]
UNICODE = [
    "Ünïcödé naïve café résumé",
    "cafe\u0301 e\u0301 n\u0303 (decomposed: NFC must compose)",
    "Å vs A\u030a and ﬁ ligature and ① circled",
    "你好，世界！这是一个测试。",
    "日本語のテキストとカタカナ、ひらがな。",
    "한국어 텍스트입니다.",
    "Здравствуй, мир! Ελληνικά.",
    "مرحبا بالعالم ‏שלום עולם",
    "emoji 😀🎉👩‍💻🇺🇸 and skin tone 👍🏽",
    "math ∑∫√∞≈≠≤≥ and arrows →←↑↓ and ℝℕℤ",
    "हिन्दी देवनागरी के साथ मात्राएँ",
    "\U0001F600\U0001F601 surrogate-plane characters",
]
JSON_TOOLS = [
    '{"name": "shell", "arguments": {"command": "ls -la", "timeout": 30}}',
    '<tool_call>\n{"name": "read_file", "arguments": {"path": "src/main.py"}}\n</tool_call>',
    '{"tool_calls":[{"id":"call_1","type":"function","function":{"name":"grep","arguments":"{\\"pattern\\":\\"TODO\\"}"}}]}',
    '[{"a":1,"b":[true,false,null],"c":{"d":"e\\u00e9"}}]',
]
SPECIAL = [
    "<|im_start|>user\nHi<|im_end|>\n<|im_start|>assistant\n",
    "<|endoftext|>",
    "text<|im_end|>more",
    "<think>\nreasoning here\n</think>\n\nanswer",
    "<|im_start|><|im_start|> doubled <|im_end|><|im_end|>",
    "<|im_start  not special |im_end|>",
    "<|vision_start|><|image_pad|><|vision_end|>",
]
CATEGORIES = {"plain": PLAIN, "code": CODE, "punctuation": PUNCTUATION, "whitespace": WHITESPACE, "unicode": UNICODE,
              "json_tools": JSON_TOOLS, "special": SPECIAL}

TOOLS = [{"type": "function", "function": {"name": "shell", "description": "Run a shell command",
                                           "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                                                          "required": ["command"]}}}]
CONVERSATIONS = {
    "single": [{"role": "user", "content": "What is 2 + 2?"}],
    "system": [{"role": "system", "content": "You are a careful coding agent."}, {"role": "user", "content": "List the files."}],
    "multi_turn": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"},
                   {"role": "assistant", "content": "Hello! How can I help?"}, {"role": "user", "content": "Tell me a joke about compilers."}],
    "tool_round_trip": [{"role": "system", "content": "You can run shell commands."},
                        {"role": "user", "content": "How many files are in /tmp?"},
                        {"role": "assistant", "content": "",
                         "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "shell", "arguments": {"command": "ls /tmp | wc -l"}}}]},
                        {"role": "tool", "tool_call_id": "call_1", "content": "42\n"},
                        {"role": "user", "content": "Thanks — and in ~/Downloads? Ünïcödé 你好"}],
}
TEMPLATE_KWARGS = {
    "thinking_off": {"enable_thinking": False},
    "thinking_low": {"enable_thinking": True, "reasoning_effort": "low"},
    "thinking_medium": {"enable_thinking": True, "reasoning_effort": "medium"},
}


def native_ids(cli: str, gguf: str, text: str) -> list[int]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
        handle.write(text)
        path = handle.name
    try:
        result = subprocess.run([cli, "-m", gguf, "--dump-tokens", "--raw", "--prompt-file", path],
                                capture_output=True, timeout=120)
    finally:
        Path(path).unlink()
    if result.returncode != 0:
        raise RuntimeError("native tokenizer failed: " + result.stderr.decode(errors="replace")[-400:])
    first = result.stdout.decode().splitlines()[0]
    return json.loads(first)


def splash_tokenizer(splash_src: str, gguf: str, workdir: Path):
    sys.path.insert(0, splash_src)
    from install import gguf as splash_gguf  # noqa: PLC0415
    from server.tokenization import tokenizable  # noqa: PLC0415
    from transformers import AutoTokenizer  # noqa: PLC0415

    files = splash_gguf.tokenizer_files(splash_gguf.Metadata(gguf))
    for name, data in files.items():
        (workdir / name).parent.mkdir(parents=True, exist_ok=True)
        (workdir / name).write_bytes(data)
    tokenizer = AutoTokenizer.from_pretrained(workdir / "tokenizer", local_files_only=True, trust_remote_code=False)
    return tokenizer, (lambda text: tokenizer(tokenizable(text), add_special_tokens=False)["input_ids"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gguf", required=True)
    parser.add_argument("--native-cli", required=True)
    parser.add_argument("--splash-src", required=True)
    parser.add_argument("--reference")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        try:
            tokenizer, encode = splash_tokenizer(args.splash_src, args.gguf, Path(folder))
        except Exception as error:  # the profile is refused: report it as the result
            print(f"Splash cannot derive a tokenizer: {type(error).__name__}: {error}")
            if args.json:
                Path(args.json).write_text(json.dumps({"ok": False, "splash_error": str(error)}, indent=1) + "\n")
            return 1
        reference = None
        if args.reference:
            from tokenizers import Tokenizer  # noqa: PLC0415

            original = Tokenizer.from_file(str(Path(args.reference) / "tokenizer.json"))
            reference = lambda text: original.encode(text, add_special_tokens=False).ids  # noqa: E731
        cases = [(category, text) for category, texts in CATEGORIES.items() for text in texts]
        for conversation, messages in CONVERSATIONS.items():
            for mode, kwargs in TEMPLATE_KWARGS.items():
                rendered = tokenizer.apply_chat_template(messages, tools=TOOLS if conversation == "tool_round_trip" else None,
                                                         tokenize=False, add_generation_prompt=True, **kwargs)
                cases.append((f"template:{conversation}:{mode}", rendered))
        for category, text in cases:
            native, splash = native_ids(args.native_cli, args.gguf, text), encode(text)
            extra = {}
            if reference:
                ids = reference(text)
                extra = {"reference_matches_native": ids == native, "reference_matches_splash": ids == splash}
            first = next((i for i, (a, b) in enumerate(zip(native, splash)) if a != b), None if len(native) == len(splash) else min(len(native), len(splash)))
            rows.append({"category": category, "text": text[:80], "tokens": len(native), "match": native == splash, **extra,
                         **({} if native == splash else {"first_difference": first, "native": native[max(0, (first or 0) - 3):(first or 0) + 5],
                                                         "splash": splash[max(0, (first or 0) - 3):(first or 0) + 5]})})
    failed = [r for r in rows if not r["match"]]
    by_category: dict[str, list[int]] = {}
    for row in rows:
        key = row["category"].split(":")[0]
        by_category.setdefault(key, [0, 0])
        by_category[key][0] += row["match"]
        by_category[key][1] += 1
    for key, (good, total) in by_category.items():
        print(f"{key:<12} {good}/{total}")
    for row in failed:
        print("MISMATCH", json.dumps(row, ensure_ascii=False))
    summary = {"ok": not failed, "cases": len(rows), "matched": len(rows) - len(failed), "tokens": sum(r["tokens"] for r in rows),
               "by_category": by_category, "rows": rows}
    if reference:
        summary["reference"] = {"matches_native": sum(r["reference_matches_native"] for r in rows),
                                "matches_splash": sum(r["reference_matches_splash"] for r in rows)}
        for row in rows:
            if not (row["reference_matches_native"] and row["reference_matches_splash"]):
                print("REFERENCE DIFFERS", json.dumps({k: row[k] for k in ("category", "text", "reference_matches_native", "reference_matches_splash")}, ensure_ascii=False))
        print(f"reference: native {summary['reference']['matches_native']}/{len(rows)}, splash {summary['reference']['matches_splash']}/{len(rows)}")
    print(f"parity: {summary['matched']}/{summary['cases']} cases, {summary['tokens']} native tokens")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    reference_ok = not reference or summary["reference"]["matches_splash"] == len(rows)
    return 0 if not failed and reference_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
