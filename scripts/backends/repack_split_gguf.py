"""Lossless repack: a DwarfStar main GGUF plus its standalone PLE GGUF -> the published two-part split layout.

DwarfStar's Swift and Cyber builds keep the per-layer embedding (PLE) table in its own GGUF; the published
Scope GGUF (and the Splash engine) uses a two-part split instead: part 1 holds every other tensor and all
metadata, part 2 holds only `per_layer_token_embd.weight`. This tool writes that layout without touching a
single weight: tensor payloads are copied byte for byte (gguf-py's own copy routine), the split keys follow
the published Scope files exactly, and the result is verified tensor by tensor against the sources.

    repack_split_gguf.py MAIN.gguf PLE.gguf OUT_DIR STEM

Run it with the gguf-py from the pinned llama.cpp checkout (the Cyber conversion toolchain) on PYTHONPATH.
Writes OUT_DIR/STEM-00001-of-00002.gguf and OUT_DIR/STEM-00002-of-00002.gguf, then prints a JSON receipt.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import gguf
from gguf import GGUFReader, GGUFValueType, GGUFWriter

SPLIT_KEYS = {"split.no": GGUFValueType.UINT16, "split.count": GGUFValueType.UINT16, "split.tensors.count": GGUFValueType.INT32}


def copy_metadata(reader: GGUFReader, writer: GGUFWriter) -> None:
    for field in reader.fields.values():
        if field.name == gguf.Keys.General.ARCHITECTURE or field.name.startswith("GGUF.") or field.name in SPLIT_KEYS:
            continue
        value_type = field.types[0]
        sub_type = field.types[-1] if value_type == GGUFValueType.ARRAY else None
        writer.add_key_value(field.name, field.contents(), value_type, sub_type=sub_type)


def write_part(path: Path, reader: GGUFReader, part: int, total_tensors: int, metadata: bool) -> None:
    arch = reader.fields.get(gguf.Keys.General.ARCHITECTURE)
    writer = GGUFWriter(str(path), arch.contents() if arch else "qwen4exp")
    if not metadata:  # the published part 2 carries only the three split keys
        writer.kv_data[0].pop(gguf.Keys.General.ARCHITECTURE, None)
    else:
        copy_metadata(reader, writer)
    writer.add_key_value("split.no", part, SPLIT_KEYS["split.no"])
    writer.add_key_value("split.count", 2, SPLIT_KEYS["split.count"])
    writer.add_key_value("split.tensors.count", total_tensors, SPLIT_KEYS["split.tensors.count"])
    for tensor in reader.tensors:
        writer.add_tensor_info(tensor.name, tensor.data.shape, tensor.data.dtype, tensor.data.nbytes, tensor.tensor_type)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_ti_data_to_file()
    for tensor in reader.tensors:
        writer.write_tensor_data(tensor.data, tensor_endianess=reader.endianess)
    writer.close()


def digest(reader: GGUFReader) -> dict[str, str]:
    return {t.name: hashlib.sha256(memoryview(t.data).cast("B")).hexdigest() for t in reader.tensors}


def main(argv: list[str]) -> int:
    main_path, ple_path, out_dir, stem = Path(argv[0]), Path(argv[1]), Path(argv[2]), argv[3]
    out_dir.mkdir(parents=True, exist_ok=True)
    main_reader, ple_reader = GGUFReader(str(main_path), "r"), GGUFReader(str(ple_path), "r")
    if [t.name for t in ple_reader.tensors] != ["per_layer_token_embd.weight"]:
        raise SystemExit("PLE file must hold exactly per_layer_token_embd.weight")
    if any(t.name == "per_layer_token_embd.weight" for t in main_reader.tensors):
        raise SystemExit("main file already holds the PLE table")
    total = len(main_reader.tensors) + 1
    part1, part2 = out_dir / f"{stem}-00001-of-00002.gguf", out_dir / f"{stem}-00002-of-00002.gguf"
    write_part(part1, main_reader, 0, total, metadata=True)
    write_part(part2, ple_reader, 1, total, metadata=False)
    # verification: same tensors, same bytes, same metadata (apart from the split keys)
    new1, new2 = GGUFReader(str(part1), "r"), GGUFReader(str(part2), "r")
    tensors_equal = digest(main_reader) == digest(new1) and digest(ple_reader) == digest(new2)
    meta = lambda r: {f.name: str(f.contents()) for f in r.fields.values() if not f.name.startswith("GGUF.") and f.name not in SPLIT_KEYS}  # noqa: E731
    receipt = {"sources": {"main": str(main_path), "ple": str(ple_path)}, "parts": [str(part1), str(part2)],
               "tensors": total, "tensor_payloads_identical": tensors_equal, "metadata_identical": meta(main_reader) == meta(new1),
               "part2_keys": sorted(f.name for f in new2.fields.values() if not f.name.startswith("GGUF."))}
    print(json.dumps(receipt, indent=1))
    return 0 if tensors_equal and receipt["metadata_identical"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
