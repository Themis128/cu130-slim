"""Proactive re-login/session check. `python relogin.py [config.json]`."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine

HERE = os.path.dirname(os.path.abspath(__file__))
cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "portal.json")
engine.run_relogin(engine.load_config(cfg_path))
