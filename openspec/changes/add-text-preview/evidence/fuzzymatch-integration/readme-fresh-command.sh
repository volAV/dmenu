set -eu
make config.h
cc -std=c99 -pedantic -Wall -g -O1 -fno-omit-frame-pointer \
    -fsanitize=address,undefined -D_DEFAULT_SOURCE -D_XOPEN_SOURCE=700 \
    -DVERSION='"5.4"' -DXINERAMA -I/usr/include/freetype2 \
    tests/fuzzymatch.c drw.c util.c \
    -lX11 -lXinerama -lfontconfig -lXft -lm -o /tmp/dmenu-fuzzy-integration-mclmbvjk/readme-fresh/fuzzymatch-check
ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1 \
    /tmp/dmenu-fuzzy-integration-mclmbvjk/readme-fresh/fuzzymatch-check
ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1 \
    /tmp/dmenu-fuzzy-integration-mclmbvjk/readme-fresh/fuzzymatch-check a a

