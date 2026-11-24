# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Checks on the documentation blocks of every module.

ansible-lint reads the EXAMPLES of a module as YAML only, so nothing else
fails when an example is no valid task.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import ast
import json
import os
import re

import pytest
import yaml

MODULES_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "plugins", "modules"
)
MODULES = sorted(
    name[:-3]
    for name in os.listdir(MODULES_DIR)
    if name.startswith("pure1_") and name.endswith(".py")
)
# What a task of an example holds besides its module
TASK_KEYWORDS = {"name", "register", "loop", "when", "vars"}


def _assignments(module):
    """Return the module-level string assignments of a module file by name"""
    with open(os.path.join(MODULES_DIR, module + ".py")) as source:
        tree = ast.parse(source.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
    return found


def _examples(module):
    """Return the tasks of the EXAMPLES of a module"""
    return yaml.safe_load(ast.literal_eval(_assignments(module)["EXAMPLES"]))


def test_every_module_is_checked():
    assert len(MODULES) >= 10
    assert "pure1_info" in MODULES


@pytest.mark.parametrize("module", MODULES)
def test_examples_name_every_module_in_full(module):
    for task in _examples(module):
        (action,) = set(task) - TASK_KEYWORDS
        assert action.count(".") == 2, task["name"]


@pytest.mark.parametrize("module", MODULES)
def test_examples_register_at_task_level(module):
    for task in _examples(module):
        (action,) = set(task) - TASK_KEYWORDS
        assert "register" not in (task[action] or {}), task["name"]


@pytest.mark.parametrize("module", MODULES)
def test_examples_read_only_what_an_earlier_task_registered(module):
    registered = set()
    for task in _examples(module):
        known = registered | set(task.get("vars", {}))
        if "loop" in task:
            known.add("item")
        read = set(re.findall(r"\{\{\s*(\w+)", json.dumps(task)))
        assert read <= known, task["name"]
        if "register" in task:
            registered.add(task["register"])


@pytest.mark.parametrize("module", MODULES)
def test_module_has_no_ansible_metadata(module):
    assert "ANSIBLE_METADATA" not in _assignments(module)


def _readme_modules():
    """Return the names in the list under "Available Modules" of README.md"""
    readme = os.path.join(MODULES_DIR, os.pardir, os.pardir, "README.md")
    with open(readme, encoding="utf-8") as source:
        section = source.read().split("### Available Modules\n", 1)[1]
    # the list ends at the next heading
    section = re.split(r"^#", section, maxsplit=1, flags=re.MULTILINE)[0]
    return re.findall(r"^- (\S+)", section, flags=re.MULTILINE)


def test_readme_lists_every_module_once():
    """Each file of plugins/modules exactly once, and nothing else"""
    files = sorted(
        name[:-3]
        for name in os.listdir(MODULES_DIR)
        if name.endswith(".py") and name != "__init__.py"
    )

    assert sorted(_readme_modules()) == files
