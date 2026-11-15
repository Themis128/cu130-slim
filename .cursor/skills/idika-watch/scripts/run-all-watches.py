"""Run one check for every watches/*.json (or given configs).

`python run-all-watches.py [config.json ...]` — defaults to all
watches/*.json beside this script. Suitable as a single schtasks target
when several portals share the deployment."""
import glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine

paths = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "watches", "*.json")))
if not paths:
    print("no watch configs found")
    sys.exit(1)

rc = 0
for p in paths:
    try:
        cfg = engine.load_config(p)
        print(f"--- {cfg.get('name', p)} ---")
        engine.run_check(cfg)
    except Exception as e:
        print(f"{p}: ERROR {e}")
        rc = 1
sys.exit(rc)
