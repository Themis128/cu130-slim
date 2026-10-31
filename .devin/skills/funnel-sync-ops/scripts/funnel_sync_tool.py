import sys, subprocess
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
a = sys.argv[1] if len(sys.argv)>1 else "status"
if a=="status":
    print(run("celery -A app.worker.celery_app inspect registered 2>/dev/null | grep -i funnel"))
    print(run("celery -A app.worker.celery_app inspect scheduled 2>/dev/null | grep -i funnel"))
elif a=="trigger":
    from app.worker.celery_app import celery_app
    r=celery_app.send_task("app.worker.tasks.funnel_sync.run_funnel_sync"); print("task:",r.id)
elif a=="logs":
    print(run("docker logs social-worker-default --tail 30 2>&1 | grep -iE 'funnel|mautic|lake'"))
else:
    print("usage: status|trigger|check|logs")
