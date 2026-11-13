#!/bin/sh
# Headless UI checks with the Playwright and Chromium already installed for the agent tools.
#   run.sh viz_bounds      Visualizer: every text, MTP draft and output token stays inside its panel (synthetic ticks, test page only)
#   run.sh ui_profiles     Run view: all seven profiles selectable; chats listed; no horizontal overflow
#   run.sh ui_regression   Clicks Cyber MEDIUM -> Stop -> LOW -> Start through the UI and compares the window with the controller (starts a model)
set -e
here="$(cd "$(dirname "$0")" && pwd -P)"
tools="$here/../../../agent-tools"
export PLAYWRIGHT_BROWSERS_PATH="$tools/cache/browsers" PW="$tools/node_modules/playwright/index.mjs"
exec node "$here/${1:?name a check}.mjs"
