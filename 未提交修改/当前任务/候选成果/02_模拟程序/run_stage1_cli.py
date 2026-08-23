#!/usr/bin/env python3
import sys
from airvortex.stage1 import install_stage1_overlay
install_stage1_overlay()
from airvortex.cli import main
raise SystemExit(main(sys.argv[1:]))
