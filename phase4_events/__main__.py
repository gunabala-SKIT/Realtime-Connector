"""`python -m phase4_events` runs the API. Use `-m phase4_events.app` for the pipeline."""
import sys

from .api import main

sys.exit(main())
