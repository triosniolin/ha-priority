#!/usr/bin/env bash
# Card tests in a real browser (Playwright + headless Chromium).
#
#   ./tests/run_e2e.sh
#
# The node suite in card_test.js runs against a hand-rolled DOM stub with no
# layout, no CSS and no real event dispatch. It has passed code that could not
# work more than once. Anything about clicking, opening or positioning belongs
# here instead.
#
# Setup:  npm i -D playwright && npx playwright install chromium
#
# On a box without root, `playwright install-deps` cannot run; unpack the .debs
# into a prefix and point PLAYWRIGHT_LIB_DIR at its lib directory instead.
set -euo pipefail

LIBS="${PLAYWRIGHT_LIB_DIR:-$HOME/.local/share/playwright-libs/usr/lib/x86_64-linux-gnu}"
if [ -d "$LIBS" ]; then
  export LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec node "$(dirname "$0")/e2e_test.js" "$@"
