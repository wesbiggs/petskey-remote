# PETSKEY Remote. Needs ACME 0.97 (apt install acme / brew install acme) and Python 3.
#   make          build/petskey-remote.prg and build/petskey-remote.d64
#   make test     host-side unit tests
ACME ?= acme
PYTHON ?= python3

GEN := c64/gen/consts.inc c64/gen/layout.inc c64/gen/font.inc c64/gen/version.inc \
       c64/gen/PETSKEY.K64 c64/gen/PETSKEY.K128

all: build/petskey-remote.d64

# Always run: the version comes from git (or PETSKEY_VERSION); files that come out
# the same are left alone, so nothing else rebuilds.
$(GEN) &: FORCE
	@$(PYTHON) tools/petskey_build.py gen c64/gen

FORCE:

build/petskey-remote.prg: c64/petskey.asm $(GEN)
	mkdir -p build
	cd c64 && $(ACME) -r ../build/report --vicelabels ../build/labels --format cbm -o ../$@ petskey.asm

build/petskey-remote.d64: build/petskey-remote.prg $(GEN)
	$(PYTHON) tools/petskey_build.py d64 $@ \
		"build/petskey-remote.prg:PETSKEY REMOTE" \
		c64/gen/PETSKEY.K64 c64/gen/PETSKEY.K128

test:
	$(PYTHON) -m unittest discover -s host
	$(PYTHON) -m unittest discover -s tools

# End-to-end in VICE (needs x64sc/x128 with ROMs, Xvfb, xdotool, ImageMagick)
e2e: build/petskey-remote.d64
	$(PYTHON) test/vice_e2e.py --machine c64
	$(PYTHON) test/vice_e2e.py --machine c128

clean:
	rm -rf build c64/gen

.PHONY: all test clean e2e FORCE
