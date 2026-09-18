import os
from dotenv import load_dotenv

# Always load backend/.env, even if cwd is the repo root. override=True is
# required for admin "save then restart": the dying process already put the
# previous MAAS_* values into os.environ, the helper inherits them, and
# python-dotenv would otherwise ignore the newly saved file.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

# ── 安全 ──────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production-please-use-random-32chars")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30天

# ── MaaS API ──────────────────────────────────
MAAS_BASE_URL = os.getenv("MAAS_BASE_URL", "")   # 从MaaS平台复制，例如 https://xxx.com/api
MAAS_APP_KEY  = os.getenv("MAAS_APP_KEY", "")    # 应用KEY

# ── 路径 ──────────────────────────────────────
# The legacy ``uploads`` directory can be left behind as a corrupt NTFS entry
# on some workspace restores (WinError 1392).  Keep it untouched and use a
# fresh runtime directory for new uploads so one damaged directory cannot
# make every analysis request fail before parsing starts.
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads_runtime")
# The legacy ``reports`` entry is also corrupt in this recovered workspace.
# Keep it untouched and generate new local HTML reports in a healthy runtime
# directory, so report generation never depends on MaaS or the damaged entry.
REPORT_DIR  = os.path.join(BASE_DIR, "reports_runtime")
BACKUP_DIR  = os.path.join(BASE_DIR, "backups")
STATIC_DIR  = os.path.join(BASE_DIR, "static")
DB_PATH     = os.path.join(BASE_DIR, "qc.db")

# ── 分析参数 ──────────────────────────────────
ANALYSIS_CONCURRENCY = int(os.getenv("ANALYSIS_CONCURRENCY", "5"))  # 同时分析几条
# A complete Excel slice can contain a long conversation and translation
# output. Keep this configurable while using a safer default than the former
# 60-second cutoff.
MAAS_TIMEOUT_SECONDS = int(os.getenv("MAAS_TIMEOUT_SECONDS", "200"))
MAX_UPLOAD_MB = 100
