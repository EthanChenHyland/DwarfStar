#!/usr/bin/env python3
"""Calibrated Q2_0 encoding of the routed experts (GPTQ with the Q2_0 block as the group).

The container is unchanged: GGUF type Q2_0, 64 weights per 18-byte block, one
binary16 scale ``d`` and levels ``-d, 0, +d, +2d``. Only the choice of scale and
codes changes. Instead of minimising weight error block by block, each
projection is quantized column by column with second-order error feedback
(Frantar et al., GPTQ), using the second moments of that projection's real
inputs collected by a calibration capture (``capture.py``):

* gate and up projections of a layer share the layer's expert-input Hessian;
* the down projection uses the Hessian of the layer's expert activations.

Within a block the scale is chosen by the exact searched-Q2_0 solver on the
error-compensated weights, weighted by the GPTQ column metric.

Source values are always the pinned checkpoint's NVFP4 tensors, decoded
exactly. Nothing else enters the result: calibration data affects only how
rounding error is distributed.

    gptq_q2_0.py --stats DIR --layers 0-47 --out DIR [--damp 0.01] [--pilot N]
                 [--holdout DIR] [--current-gguf FILE] [--legacy-tools DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HEADER_WORDS = 12
MAGIC = 0x3142494C41433444  # "D4CALIB1"
PROJECTIONS = ("gate", "up", "down")
BLOCK = 64
ROW_BYTES_PER_BLOCK = 18


def read_stats(directory: Path, layer: int) -> dict:
    """One layer's calibration statistics as written by the runtime's capture mode."""
    path = directory / f"calib-layer-{layer:02d}.bin"
    with path.open("rb") as stream:
        header = np.frombuffer(stream.read(8 * HEADER_WORDS), dtype="<u8")
        if int(header[0]) != MAGIC or int(header[1]) != layer:
            raise ValueError(f"{path}: not a calibration file for layer {layer}")
        embd, ff, experts = int(header[2]), int(header[3]), int(header[4])
        seen_x, seen_mid, n_x, n_mid = (int(v) for v in header[5:9])
        h_in = np.frombuffer(stream.read(8 * embd * embd), dtype="<f8").reshape(embd, embd)
        h_mid = np.frombuffer(stream.read(8 * ff * ff), dtype="<f8").reshape(ff, ff)
        mid_diag = np.frombuffer(stream.read(8 * experts * ff), dtype="<f8").reshape(experts, ff)
        counts = np.frombuffer(stream.read(8 * experts), dtype="<u8")
        x = np.frombuffer(stream.read(4 * n_x * embd), dtype="<f4").reshape(n_x, embd)
        mid = np.frombuffer(stream.read(4 * n_mid * ff), dtype="<f4").reshape(n_mid, ff)
        mid_expert = np.frombuffer(stream.read(4 * n_mid), dtype="<i4")

    def symmetric(upper: np.ndarray, count: int) -> np.ndarray:
        full = np.triu(upper) + np.triu(upper, 1).T
        return full / max(count, 1)

    return {
        "tokens": seen_x, "mid_rows": seen_mid,
        "h_in": symmetric(h_in, seen_x), "h_mid": symmetric(h_mid, seen_mid),
        "mid_diag": mid_diag, "expert_count": counts,
        "x_samples": x, "mid_samples": mid, "mid_expert": mid_expert,
    }


def inverse_factor(hessian: np.ndarray, damp: float) -> tuple[np.ndarray, np.ndarray]:
    """Upper Cholesky factor U of the damped inverse Hessian (H^-1 = U^T U) and the dead-column mask."""
    h = np.array(hessian, dtype=np.float64)
    diag = np.diag(h).copy()
    dead = diag <= 0
    h[dead, :] = 0
    h[:, dead] = 0
    h[dead, dead] = 1.0
    h[np.diag_indices_from(h)] += damp * float(np.mean(diag[~dead])) if (~dead).any() else 1.0
    lower = np.linalg.cholesky(h)
    identity = np.eye(h.shape[0])
    inverse_lower = np.linalg.solve(lower, identity)
    h_inverse = inverse_lower.T @ inverse_lower
    upper = np.ascontiguousarray(np.linalg.cholesky(h_inverse).T)  # h_inverse = upper^T @ upper
    return upper.astype(np.float32), dead


def block_order(hessian: np.ndarray, columns: int, mode: str) -> np.ndarray:
    """Column permutation that keeps every 64-column block intact.

    ``natural`` keeps the stored order. ``active`` visits blocks, and columns
    inside a block, by decreasing input energy so the best-conditioned
    columns absorb the least error.
    """
    if mode == "natural":
        return np.arange(columns)
    diag = np.diag(hessian)
    blocks = np.arange(columns).reshape(-1, BLOCK)
    order = np.argsort(-diag[blocks].mean(axis=1), kind="stable")
    return np.concatenate([blocks[b][np.argsort(-diag[blocks[b]], kind="stable")] for b in order])


def gptq_matrix(weights: np.ndarray, upper: np.ndarray, dead: np.ndarray, search) -> tuple[np.ndarray, np.ndarray]:
    """Quantize ``weights`` [rows, cols] in place of rounding-to-nearest.

    Returns (scales binary16 bits [rows, blocks], levels int8 [rows, cols]) in
    the column order of ``weights``.
    """
    rows, columns = weights.shape
    blocks = columns // BLOCK
    work = np.array(weights, dtype=np.float32, order="C")
    work[:, dead] = 0.0
    udiag = np.diag(upper).copy()
    levels = np.empty((rows, columns), dtype=np.int8)
    scale_bits = np.empty((rows, blocks), dtype="<u2")
    error = np.empty((rows, BLOCK), dtype=np.float32)
    for b in range(blocks):
        i1, i2 = b * BLOCK, (b + 1) * BLOCK
        part = np.ascontiguousarray(work[:, i1:i2])
        importance = (1.0 / np.square(udiag[i1:i2])).astype(np.float32)
        importance /= importance.max()
        raw, _, _ = search.encode(part, importance=importance)
        bits = raw.reshape(rows, ROW_BYTES_PER_BLOCK)[:, :2].copy().view("<u2").reshape(rows)
        scale = bits.view("<f2").astype(np.float32)
        scale_bits[:, b] = bits
        nonzero = scale != 0
        divisor = np.where(nonzero, scale, 1.0).astype(np.float32)
        for j in range(BLOCK):
            column = part[:, j]
            level = np.clip(np.rint(column / divisor), -1, 2)
            level[~nonzero] = 0
            residual = (column - level * scale) / udiag[i1 + j]
            levels[:, i1 + j] = level
            error[:, j] = residual
            if j + 1 < BLOCK:
                part[:, j + 1:] -= residual[:, None] * upper[i1 + j, i1 + j + 1:i2][None, :]
        if i2 < columns:
            work[:, i2:] -= error @ upper[i1:i2, i2:]
    return scale_bits, levels


def pack(scale_bits: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Q2_0 bytes for scales [rows, blocks] and levels [rows, cols] (stored column order)."""
    rows, columns = levels.shape
    blocks = columns // BLOCK
    codes = (levels.astype(np.int16) + 1).astype(np.uint8).reshape(rows, blocks, 16, 4)
    packed = codes[..., 0] | (codes[..., 1] << 2) | (codes[..., 2] << 4) | (codes[..., 3] << 6)
    out = np.empty((rows, blocks, ROW_BYTES_PER_BLOCK), dtype=np.uint8)
    out[..., :2] = scale_bits.reshape(rows, blocks, 1).view(np.uint8).reshape(rows, blocks, 2)
    out[..., 2:] = packed
    return out.reshape(-1)


def encode_projection(reference: np.ndarray, upper: np.ndarray, dead: np.ndarray, order: np.ndarray, search) -> np.ndarray:
    """Calibrated Q2_0 bytes for one stack of expert matrices [rows, cols]."""
    permuted = reference[:, order]
    bits_permuted, levels_permuted = gptq_matrix(permuted, upper, dead, search)
    rows, columns = reference.shape
    levels = np.empty_like(levels_permuted)
    levels[:, order] = levels_permuted
    block_of = order.reshape(-1, BLOCK)[:, 0] // BLOCK  # stored block index of each processed block
    bits = np.empty_like(bits_permuted)
    bits[:, block_of] = bits_permuted
    return pack(bits, levels)


def relative_output_error(reference: np.ndarray, decoded: np.ndarray, samples: np.ndarray) -> float:
    """||X (W - Q)^T|| / ||X W^T|| on held-out input rows."""
    true = samples @ reference.T
    return float(np.linalg.norm(samples @ (reference - decoded).T) / max(np.linalg.norm(true), 1e-30))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", required=True, type=Path)
    parser.add_argument("--holdout", type=Path, help="statistics from different text, used only to measure error")
    parser.add_argument("--layers", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--damp", type=float, default=0.01)
    parser.add_argument("--order", choices=("natural", "active"), default="natural")
    parser.add_argument("--pilot", type=int, default=0, help="encode only this many experts per projection and report errors")
    parser.add_argument("--batch", type=int, default=64, help="experts quantized together")
    parser.add_argument("--current-gguf", type=Path, help="existing main file, to measure the current encoding")
    parser.add_argument("--legacy-tools", type=Path, required=True, help="directory with nvfp4_source.py and quant_libs.py")
    parser.add_argument("--gguf-py", type=Path)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()

    sys.path.insert(0, str(args.legacy_tools))
    import nvfp4_source as source  # exact NVFP4 decoder for the pinned checkpoint
    from quant_libs import Q2Search

    search = Q2Search()
    current = None
    if args.current_gguf:
        if args.gguf_py:
            sys.path.insert(0, str(args.gguf_py))
        from gguf import GGUFReader

        current = {t.name: t for t in GGUFReader(str(args.current_gguf)).tensors}
    layers = []
    for part in args.layers.split(","):
        if "-" in part:
            first, last = part.split("-")
            layers += list(range(int(first), int(last) + 1))
        else:
            layers.append(int(part))
    args.out.mkdir(parents=True, exist_ok=True)

    for layer in layers:
        receipt_path = args.out / f"layer-{layer:02d}.json"
        if receipt_path.exists() and not args.pilot:
            print(f"layer {layer}: already encoded", flush=True)
            continue
        started = time.time()
        stats = read_stats(args.stats, layer)
        holdout = read_stats(args.holdout, layer) if args.holdout else stats
        layer_source = source.LayerSource(layer, **({"source_dir": str(args.source_dir)} if args.source_dir else {}))
        receipt = {"layer": layer, "damp": args.damp, "order": args.order, "calibration_tokens": stats["tokens"],
                   "holdout": bool(args.holdout), "projections": {}}
        layer_dir = args.out / f"layer-{layer:02d}"
        layer_dir.mkdir(exist_ok=True)
        for projection in PROJECTIONS:
            hessian = stats["h_mid"] if projection == "down" else stats["h_in"]
            upper_natural, dead = inverse_factor(hessian, args.damp)
            order = block_order(hessian, hessian.shape[0], args.order)
            if args.order != "natural":
                upper, dead_ordered = inverse_factor(hessian[np.ix_(order, order)], args.damp)
            else:
                upper, dead_ordered = upper_natural, dead
            samples = holdout["mid_samples"] if projection == "down" else holdout["x_samples"]
            samples = np.asarray(samples[:1024], dtype=np.float32)
            experts = range(args.pilot) if args.pilot else range(source.N_EXPERTS)
            name = f"blk.{layer}.ffn_{projection}_exps.weight"
            digest = hashlib.sha256()
            metrics = {"weight_sq_error": 0.0, "weight_sq": 0.0, "output_error": [], "output_error_current": [],
                       "weight_error_current_sq": 0.0}
            total_bytes = 0
            raw_path = layer_dir / (name + ".raw")
            partial = raw_path.with_suffix(".partial")
            handle = None if args.pilot else partial.open("wb")
            pending: list[tuple[int, np.ndarray]] = []

            def flush() -> None:
                nonlocal total_bytes
                if not pending:
                    return
                stack = np.concatenate([reference for _, reference in pending], axis=0)
                rows_each = pending[0][1].shape[0]
                encoded = encode_projection(stack, upper, dead_ordered, order, search)
                decoded = search.decode(encoded, stack.shape[0], stack.shape[1])
                metrics["weight_sq_error"] += float(np.square(stack - decoded, dtype=np.float64).sum())
                metrics["weight_sq"] += float(np.square(stack, dtype=np.float64).sum())
                for index, (expert, reference) in enumerate(pending):
                    if expert % 16 == 0:
                        new = decoded[index * rows_each:(index + 1) * rows_each]
                        metrics["output_error"].append(relative_output_error(reference, new, samples))
                        if current is not None:
                            tensor = current[name]
                            per_expert = tensor.data.size // source.N_EXPERTS
                            old_raw = np.asarray(tensor.data).reshape(-1)[expert * per_expert:(expert + 1) * per_expert]
                            old = search.decode(old_raw, reference.shape[0], reference.shape[1])
                            metrics["output_error_current"].append(relative_output_error(reference, old, samples))
                            metrics["weight_error_current_sq"] += float(np.square(reference - old, dtype=np.float64).sum())
                if handle:
                    data = encoded.tobytes()
                    handle.write(data)
                    digest.update(data)
                    total_bytes += len(data)
                pending.clear()

            for expert, reference, _info in layer_source.iter_projection(projection, experts=list(experts)):
                pending.append((expert, np.array(reference, dtype=np.float32)))
                if len(pending) >= args.batch:
                    flush()
            flush()
            record = {
                "tensor": name,
                "weight_relative_rms": float(np.sqrt(metrics["weight_sq_error"] / max(metrics["weight_sq"], 1e-30))),
                "output_relative_error_mean": float(np.mean(metrics["output_error"])) if metrics["output_error"] else None,
                "output_relative_error_current_mean": float(np.mean(metrics["output_error_current"])) if metrics["output_error_current"] else None,
                "experts": len(list(experts)),
                "dead_columns": int(dead.sum()),
            }
            if handle:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                partial.replace(raw_path)
                record.update({"path": str(raw_path), "bytes": total_bytes, "sha256": digest.hexdigest()})
            receipt["projections"][projection] = record
            print(f"layer {layer} {projection}: weight rel rms {record['weight_relative_rms']:.4f} "
                  f"output err new {record['output_relative_error_mean']} current {record['output_relative_error_current_mean']}",
                  flush=True)
        layer_source.close()
        receipt["seconds"] = round(time.time() - started, 1)
        if not args.pilot:
            receipt_path.write_text(json.dumps(receipt, indent=1) + "\n")
        else:
            print(json.dumps(receipt, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
