#!/bin/zsh
# Builds the website into REG_WORK/dist from git HEAD, plus the working-tree copy
# of any frontend files named on the command line (to test your own change
# without the rest of a shared working tree):
#   ./build.sh                                   # HEAD
#   ./build.sh frontend/src/components/Foo.jsx   # HEAD + your Foo.jsx
#   REG_BUILD=worktree ./build.sh                # the whole working tree
# The running server serves the new files on the next page load (no restart).
set -eu
HERE=${0:A:h}
source $HERE/env.sh
rm -rf $REG_WORK/build
mkdir -p $REG_WORK/build
if [[ "${REG_BUILD:-head}" == worktree ]]; then
  rsync -a --exclude node_modules --exclude dist --exclude dist-mobile $REG_REPO/frontend/ $REG_WORK/build/frontend/
else
  (cd $REG_REPO && git archive HEAD frontend) | tar -x -C $REG_WORK/build
  for f in "$@"; do cp $REG_REPO/$f $REG_WORK/build/$f; done
  # REG_CSS: a prepared index.css (e.g. HEAD + only your own rules) instead of HEAD's
  [[ -n "${REG_CSS:-}" ]] && cp $REG_CSS $REG_WORK/build/frontend/src/index.css
fi
ln -s $REG_REPO/frontend/node_modules $REG_WORK/build/frontend/node_modules
(cd $REG_WORK/build/frontend && nice -n 15 ./node_modules/.bin/vite build --outDir $REG_WORK/dist.new --emptyOutDir >/dev/null)
rm -rf $REG_WORK/dist && mv $REG_WORK/dist.new $REG_WORK/dist
echo "website built into $REG_WORK/dist ($(cd $REG_REPO && git rev-parse --short HEAD), ${REG_BUILD:-head}${@:+ + $*})"
