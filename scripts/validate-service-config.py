"""Validate the installed Python worker's settings without exposing their contents."""
import logging
import sys
import warnings

logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")
try:
    from redmine_mcp.config import load_settings
    from redmine_mcp.config_file import load_config_file, resolve_config_path

    if sys.argv[1:] == ["--print-path"]:
        print(resolve_config_path())
    else:
        load_settings(load_config_file(resolve_config_path()))
except Exception:
    raise SystemExit(1) from None
