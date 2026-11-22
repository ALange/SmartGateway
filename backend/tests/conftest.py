import os
import tempfile

os.environ.setdefault("SG_DATA_DIR", tempfile.mkdtemp(prefix="sgtest-"))
os.environ.setdefault("SG_ALLOW_OPEN_ADMIN", "1")   # the suite exercises the API without logging in; the fail-closed path has its own test
