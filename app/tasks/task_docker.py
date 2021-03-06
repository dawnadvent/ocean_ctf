import json
from datetime import datetime, timedelta
from io import BytesIO

from docker import APIClient, errors as docker_error

from data.database import DEFAULT_DATABASE as db
from data.models import Host
from data.models.admin import TaskList, TaskLog, RequestState
from lib.app_factory import cache
from celery_worker import app,flask_app
from lib.cache import ConstCacheKey


def docker_out_format(data):
    """
        格式化 docker输出
    """
    if not isinstance(data,dict):
        data = json.loads(data)
    if data.get("status") == 'Downloading':
        return data["progress"]
    else:
        return data.get("stream")


def task_add_log(task: int, line: dict):
    """
        处理日志存储
    """
    if isinstance(line,bytes):
        line = json.loads(line)
    task_key = "task_%s" % task
    if "progress" in line:
        cache.rpush(task_key, "%s:%s" % (line["status"], line["progress"]))
    elif "error" in line:
        cache.rpush(task_key, "%s:%s" % ("ERROR", line["error"]))
    elif "status" in line:
        cache.rpush(task_key, line["status"])


@app.task
def build_delay(task:int,host, build_type, tag, admin, pt=None, dockerfile=None):
    """
        编译镜像
    """
    with flask_app.app_context():
        task = db.query(TaskList).get(task)
        instance = db.session.query(Host).filter(Host.id == host).one_or_none()
        cli = APIClient(base_url='tcp://{}'.format(instance.addr))
        if build_type == 'tar':
            f = open(pt,'rb')
            for line in cli.build(fileobj=f, rm=True, tag=tag, custom_context=True):
                task_add_log(task.id,line)
            task.status = task.STATUS_DONE
        elif build_type == 'pull':
            for line in cli.pull(tag, stream=True, decode=True):
                task_add_log(task.id,line)
            task.status = task.STATUS_DONE
        else:
            try:
                f = BytesIO(dockerfile.encode('utf-8'))
                for line in cli.build(fileobj=f, rm=True, tag=tag):
                    task_add_log(task.id,line)
                task.status = task.STATUS_DONE
            except docker_error.DockerException as e:
                task.status = task.STATUS_ERROR
                task.remark = str(e)
        db.session.commit()

