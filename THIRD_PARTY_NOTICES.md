# Third-party notices

DwarfStar's own code is MIT licensed (see `LICENSE`). It builds on the projects below. None of their source is vendored in this repository except where stated.

| Component | Use in DwarfStar | Licence |
|---|---|---|
| [ds4 (Paperniuk fork)](https://github.com/paperniuk/ds4) @ `6ea598155f7ac1a27dad3cda5b8fc9123fdef79a` | Native Metal runtime. `native/patches/` contains diffs against its `ds4.c`, and `native/apply_hooks.py` inserts code into it at build time. The runtime binary is built locally and is not in this repository. | MIT. Copyright (c) 2026 The ds4.c authors; (c) 2023–2026 The ggml authors; (c) 2023 DeepSeek |
| [Oh My Pi](https://www.npmjs.com/org/oh-my-pi) (`omp`, `pi-coding-agent`, `pi-ai`) | ACP agent. Installed with Bun; `acp/*.ts` imports its sources through the machine-local link `acp/vendor/omp` (not tracked). | MIT |
| [Playwright](https://github.com/microsoft/playwright) | Browser tool for the agent (`agent-tools`), installed by `npm ci`, not vendored. | Apache-2.0 |
| [Puppeteer](https://github.com/puppeteer/puppeteer) | Browser tool for the agent (`agent-tools`), installed by `npm ci`, not vendored. | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) @ `2cbe0ec429b849a88664e0f62388fc92a5abd699` | Used unmodified in the Cyber conversion toolchain (outside this repository). | MIT |

## Model weights

No model weights are in this repository. Each model is subject to its publisher's licence:

* `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` (Scope)
* `ukisai/Swift1.5-Qwen3.8-Flash-Next` (Swift 1.5)
* `dealignai/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4` (source of Cyber)

Check each model card before redistributing converted files.
