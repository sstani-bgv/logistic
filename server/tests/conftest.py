import os
import tempfile

# До импорта main: своя временная база и фиктивный токен АТИ — тесты не трогают data.db и сеть.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="logistic-test-")
os.environ.setdefault("ATI_TOKEN", "test")
