"""Draft-length-adjusted decode overhead from telemetry_ab.py results (docs/DS3-GPU-TELEMETRY.md §5).

    ds3_overhead.py DIR LABEL [LABEL ...]     labels without the telemetry-ab- prefix; labels containing "ds2" are the
                                              reference

A verify cycle's cost grows with the draft rows it verifies, which depends on what the model generates, so raw ms/cycle
mixes telemetry cost with draft length. Per test, this fits the reference runs' ms/cycle = a + b * rows/cycle
(rows = drafted + 1 per cycle; cycles = decode tokens - accepted) and prints each run's residual as % of the fit.
Compare a configuration with the reference runs of the same session: sessions differ by more than configurations do.
"""
import json
import statistics as st
import sys
from pathlib import Path


def runs(directory: Path, label: str, test: str) -> list[tuple[float, float]]:
    data = json.loads((directory / f"telemetry-ab-{label}.json").read_text())
    out = []
    for row in data["rows"]:
        if row["test"] != test:
            continue
        cycles = row["decode_tokens"] - row["accepted"]
        out.append(((row["drafted"] + cycles) / cycles, row["decode_ms"] / cycles))
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    directory, labels = Path(argv[0]), argv[1:]
    for test in ("short", "decode"):
        reference = [p for label in labels if "ds2" in label for p in runs(directory, label, test)]
        xs = [x for x, _ in reference]
        mx, my = st.mean(xs), st.mean(y for _, y in reference)
        b = sum((x - mx) * (y - my) for x, y in reference) / sum((x - mx) ** 2 for x in xs)
        a = my - b * mx
        print(f"{test}: reference ms/cycle = {a:.2f} + {b:.2f} x rows/cycle (n={len(reference)})")
        for label in labels:
            residuals = [100 * (y / (a + b * x) - 1) for x, y in runs(directory, label, test)]
            print(f"   {label:24} {st.mean(residuals):+6.2f} %   runs {[round(v, 2) for v in residuals]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
