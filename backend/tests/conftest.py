import os
import tempfile

os.environ.setdefault("SG_DATA_DIR", tempfile.mkdtemp(prefix="sgtest-"))
