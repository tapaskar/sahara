import os
import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parent / "_data"
shutil.rmtree(DATA, ignore_errors=True)
os.environ["SAHARA_OFFLINE"] = "1"
os.environ["SAHARA_DATA_DIR"] = str(DATA)
os.environ["SAHARA_PUBLIC_URL"] = "https://sahara.example.test"
os.environ["SAHARA_CALLER_ID"] = "+911234567890"
os.environ.pop("SAHARA_OPERATOR_TOKEN", None)
