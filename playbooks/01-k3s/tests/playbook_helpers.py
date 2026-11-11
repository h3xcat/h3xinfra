"""Helpers for the static playbook tests of this layer.

Plain Jinja2 plus small stand-ins for the Ansible filters and tests that the
playbooks use. No hosts are contacted.
"""

import base64
import copy
import json
from pathlib import Path
import re
import time

import jinja2
from jinja2.nativetypes import NativeEnvironment
import yaml


LAYER = Path(__file__).resolve().parents[1]
SERVICE_STATE = LAYER / "tasks" / "k3s-service-state.yml"
RESTART = LAYER / "tasks" / "restart-k3s.yml"
IMPORT = "ansible.builtin.import_tasks"


def load(path):
    return yaml.safe_load(Path(path).read_text())


def to_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def environment(native=True):
    env = (NativeEnvironment if native else jinja2.Environment)(undefined=jinja2.ChainableUndefined)
    env.filters.update({
        "bool": to_bool,
        "dict2items": lambda d: [{"key": k, "value": v} for k, v in d.items()],
        "items2dict": lambda items: {i["key"]: i["value"] for i in items},
        "to_nice_yaml": lambda data, indent=4: yaml.safe_dump(data, indent=indent, default_flow_style=False),
        "dirname": lambda p: str(Path(p).parent),
        "strftime": lambda fmt, seconds, utc=False: time.strftime(fmt, (time.gmtime if utc else time.localtime)(seconds)),
        "b64decode": lambda s: base64.b64decode(s).decode(),
        "from_yaml": yaml.safe_load,
        "from_json": json.loads,
    })
    env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    return env


def conditions(task):
    when = task.get("when", [])
    return list(when) if isinstance(when, list) else [when]


def flatten(path):
    """Return the tasks of path with every import_tasks replaced by its tasks.

    An imported task gets the `when` conditions of the import before its own
    and the import `vars` under its own task vars, as Ansible applies them.
    """
    tasks = []
    for task in load(path):
        if IMPORT not in task:
            tasks.append(task)
            continue
        for child in flatten(Path(path).parent / task[IMPORT]):
            child = copy.deepcopy(child)
            when = conditions(task) + conditions(child)
            if when:
                child["when"] = when
            if task.get("vars"):
                child["vars"] = {**task["vars"], **child.get("vars", {})}
            tasks.append(child)
    return tasks


def render_vars(variables, context):
    """Render vars in definition order into a copy of context, as Ansible would."""
    context = dict(context)
    env = environment()
    for name, template in variables.items():
        context[name] = env.from_string(template).render(context) if isinstance(template, str) else template
    return context


def evaluate(expression, **context):
    return to_bool(environment().compile_expression(str(expression))(**context))


def when_true(task, context):
    context = render_vars(task.get("vars", {}), context)
    return all(evaluate(c, **context) for c in conditions(task))


def service_facts(state="active", started=2000):
    """The facts that tasks/k3s-service-state.yml sets for a systemctl show output."""
    lines = [f"ActiveState={state}", f"ActiveEnterTimestamp=@{started}" if started else "ActiveEnterTimestamp="]
    record = next(t for t in load(SERVICE_STATE) if "ansible.builtin.set_fact" in t)
    return render_vars(record["ansible.builtin.set_fact"], {"k3s_service_show": {"stdout_lines": lines}})
