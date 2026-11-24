# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_pods module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import re

import pytest
import yaml

from pypureclient import PureError

from plugins.modules import pure1_pods
from plugins.modules.pure1_pods import generate_pods_dict
from tests.unit.conftest import (
    ansible_module,
    array_reference,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


def _make_pod(array_name="array1", **overrides):
    fields = dict(
        name="pod1",
        mediator="purestorage",
        arrays=[array_reference(array_name)],
    )
    fields.update(overrides)
    return sdk_model("Pod", **fields)


def _make_stretched_pod(name="stretched", array_names=("array1", "array2")):
    return _make_pod(
        name=name, arrays=[array_reference(array) for array in array_names]
    )


POD1 = {
    "pod1": {
        "mediator": "purestorage",
        "source": [],
        "arrays": ["array1"],
        "stretched": False,
    }
}
# The pod that _make_pod("array2") builds
POD1_ON_ARRAY2 = {
    "pod1": {
        "mediator": "purestorage",
        "source": [],
        "arrays": ["array2"],
        "stretched": False,
    }
}
STRETCHED = {
    "stretched": {
        "mediator": "purestorage",
        "source": [],
        "arrays": ["array1", "array2"],
        "stretched": True,
    }
}


class TestGeneratePodsDict:
    """Tests for generate_pods_dict."""

    def test_basic_mapping_no_source(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod()])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        pure1_client.get_pods.assert_called_once_with()
        assert result["array1"] == [
            {
                "pod1": {
                    "mediator": "purestorage",
                    "source": [],
                    "arrays": ["array1"],
                    "stretched": False,
                }
            }
        ]

    def test_source_is_unwrapped_to_name(self, pure1_client):
        pod = _make_pod(source=sdk_model("FixedReference", name="sourcepod"))
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["pod1"]["source"] == "sourcepod"

    def test_source_without_name_is_no_source(self, pure1_client):
        pod = _make_pod(source=sdk_model("FixedReference", id="source-id"))
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1]}

    def test_source_with_an_empty_name_is_a_source(self, pure1_client):
        """Only a name Pure1 did not send is no source: an empty name is a name."""
        pod = _make_pod(source=sdk_model("FixedReference", name=""))
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [{"pod1": dict(POD1["pod1"], source="")}]}

    def test_array_filter_applied(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod("myarray")])

        generate_pods_dict(ansible_module(array="myarray"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='myarray'")

    def test_array_name_is_escaped_in_the_filter(self, pure1_client):
        """A quote or backslash in the array name cannot break the filter."""
        pure1_client.get_pods.return_value = valid_response([])

        result = generate_pods_dict(ansible_module(array="my'arr\\ay"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(
            filter="arrays.name='my\\'arr\\\\ay'"
        )
        assert result == {}

    def test_grouping_across_arrays(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_pod("array2")]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}

    def test_missing_mediator_defaults_to_none(self, pure1_client):
        bare = sdk_model("Pod", name="pod1", arrays=[array_reference("array1")])
        pure1_client.get_pods.return_value = valid_response([bare])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["pod1"]["mediator"] is None

    def test_pod_with_only_a_name_and_an_array(self, pure1_client):
        bare = sdk_model("Pod", name="pod1", arrays=[array_reference("array1")])
        pure1_client.get_pods.return_value = valid_response([bare])
        module = ansible_module(array=None)

        result = generate_pods_dict(module, pure1_client)

        assert result == {
            "array1": [
                {
                    "pod1": {
                        "mediator": None,
                        "source": [],
                        "arrays": ["array1"],
                        "stretched": False,
                    }
                }
            ]
        }
        module.warn.assert_not_called()

    def test_stretched_pod_listed_under_each_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_stretched_pod()]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}

    def test_stretched_pod_array_filter_first_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_stretched_pod()])

        result = generate_pods_dict(ansible_module(array="array1"), pure1_client)

        assert result == {"array1": [STRETCHED]}

    def test_stretched_pod_array_filter_second_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_stretched_pod(), _make_pod("array2")]
        )

        result = generate_pods_dict(ansible_module(array="array2"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='array2'")
        assert result == {"array2": [STRETCHED, POD1_ON_ARRAY2]}

    def test_array_filter_match_ignores_case(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_stretched_pod()])

        result = generate_pods_dict(ansible_module(array="ARRAY2"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='ARRAY2'")
        assert result == {"array2": [STRETCHED]}

    def test_array_filter_unmatched_name_keeps_pod(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_stretched_pod()]
        )

        result = generate_pods_dict(ansible_module(array="other"), pure1_client)

        assert result == {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}

    def test_entry_names_the_other_array_of_a_stretched_pod(self, pure1_client):
        """With array set the pod is under that array; arrays names them all."""
        pure1_client.get_pods.return_value = valid_response(
            [_make_stretched_pod(array_names=("array2", "array1")), _make_pod()]
        )

        result = generate_pods_dict(ansible_module(array="array1"), pure1_client)

        assert list(result) == ["array1"]
        stretched, local = result["array1"]
        assert stretched["stretched"]["arrays"] == ["array2", "array1"]
        assert stretched["stretched"]["stretched"] is True
        assert local["pod1"]["arrays"] == ["array1"]
        assert local["pod1"]["stretched"] is False

    def test_array_reference_without_name_is_skipped(self, pure1_client):
        pod = _make_pod(
            arrays=[array_reference("array1"), sdk_model("FixedReferenceFqdn")]
        )
        # The SDK reads a response without validating it, so a null entry of
        # the list arrives as None; the constructor of the model refuses one
        pod.arrays.append(None)
        pure1_client.get_pods.return_value = valid_response([pod])
        module = ansible_module(array=None)

        result = generate_pods_dict(module, pure1_client)

        assert result == {"array1": [POD1]}
        module.warn.assert_not_called()

    def test_repeated_array_name_lists_pod_once(self, pure1_client):
        pod = _make_pod(arrays=[array_reference("array1"), array_reference("array1")])
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1]}

    def test_pod_without_arrays_is_skipped(self, pure1_client):
        no_arrays = sdk_model("Pod", name="orphan", mediator="purestorage")
        pure1_client.get_pods.return_value = valid_response(
            [no_arrays, _make_pod(name="empty", arrays=[]), _make_pod()]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1]}

    def test_pod_without_arrays_gives_one_warning(self, pure1_client):
        no_arrays = sdk_model("Pod", name="orphan", mediator="purestorage")
        no_array_name = _make_pod(name="lost", arrays=[sdk_model("FixedReferenceFqdn")])
        pure1_client.get_pods.return_value = valid_response(
            [no_arrays, _make_pod(name="empty", arrays=[]), no_array_name, _make_pod()]
        )
        module = ansible_module(array=None)

        result = generate_pods_dict(module, pure1_client)

        assert result == {"array1": [POD1]}
        module.warn.assert_called_once_with(
            "Pods left out of the result: 3 without an array"
        )

    def test_pod_without_name_is_left_out_with_a_warning(self, pure1_client):
        nameless = sdk_model(
            "Pod", mediator="purestorage", arrays=[array_reference("array1")]
        )
        pure1_client.get_pods.return_value = valid_response([nameless, _make_pod()])
        module = ansible_module(array=None)

        result = generate_pods_dict(module, pure1_client)

        assert result == {"array1": [POD1]}
        module.warn.assert_called_once_with(
            "Pods left out of the result: 1 without a name"
        )

    def test_one_warning_counts_both_kinds_of_pod_left_out(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [
                sdk_model("Pod", arrays=[array_reference("array1")]),
                # neither a name nor an array: counted once, for its name
                sdk_model("Pod", mediator="purestorage"),
                sdk_model("Pod", name="orphan"),
                _make_pod(),
            ]
        )
        module = ansible_module(array="array1")

        result = generate_pods_dict(module, pure1_client)

        assert result == {"array1": [POD1]}
        module.warn.assert_called_once_with(
            "Pods left out of the result: 2 without a name, 1 without an array"
        )

    def test_no_warning_when_every_pod_is_listed(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod(), _make_stretched_pod()]
        )
        module = ansible_module(array=None)

        generate_pods_dict(module, pure1_client)

        module.warn.assert_not_called()


class TestPure1PodsMain:
    """Tests for the pure1_pods main() flow."""

    def test_pods_are_returned_by_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_stretched_pod()]
        )

        # array is left to the argument spec
        module = run_main(pure1_pods)

        pure1_client.get_pods.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_pods={"pods": {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}},
        )

    def test_array_option_selects_the_pods_of_one_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_stretched_pod()])

        module = run_main(pure1_pods, array="array2")

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='array2'")
        module.exit_json.assert_called_once_with(
            changed=False, pure1_pods={"pods": {"array2": [STRETCHED]}}
        )

    def test_pods_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod()])

        module = run_main(pure1_pods, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_pods={"pods": {"array1": [POD1]}}
        )

    def test_no_pods_gives_an_empty_result(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([])

        module = run_main(pure1_pods, array="array1")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, pure1_pods={"pods": {}})

    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_pods.return_value = error_response(403, "Access denied")

        module = run_main(pure1_pods)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message

    @pytest.mark.parametrize("params", [{}, {"array": "array1"}], ids=["all", "array"])
    def test_error_answer_fails_with_the_message_of_pure1(self, pure1_client, params):
        pure1_client.get_pods.return_value = error_response(
            400, "Illegal filter format", "Unexpected input at character 16"
        )

        module = run_main(pure1_pods, **params)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_pods failed (HTTP 400): Illegal filter format: "
            "Unexpected input at character 16"
        )

    @pytest.mark.parametrize("params", [{}, {"array": "array1"}], ids=["all", "array"])
    def test_raised_error_fails_the_task_in_one_line(self, pure1_client, params):
        pure1_client.get_pods.side_effect = PureError("connection lost")

        module = run_main(pure1_pods, **params)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_pods failed: PureError: connection lost"
        )


class TestDocumentation:
    """The documentation strings of the module against what it does."""

    def test_return_documents_the_keys_of_a_pod(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod()])
        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        documented = yaml.safe_load(pure1_pods.RETURN)["pure1_pods"]["contains"]
        assert list(documented) == ["pods"]
        keys = documented["pods"]["contains"]
        assert set(keys) == set(result["array1"][0]["pod1"])
        for new in ("arrays", "stretched"):
            assert keys[new]["description"][-1] == "Added in version 1.6.0."

    def test_sample_is_what_the_module_returns(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [
                _make_pod(),
                _make_pod(
                    name="pod2",
                    arrays=[array_reference("array1"), array_reference("array2")],
                    source=sdk_model("FixedReference", name="pod1"),
                ),
            ]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        sample = yaml.safe_load(pure1_pods.RETURN)["pure1_pods"]["sample"]
        assert sample == {"pods": result}

    def test_examples_register_at_task_level_and_read_the_result(self):
        tasks = yaml.safe_load(pure1_pods.EXAMPLES)
        module_tasks = [task for task in tasks if "everpure.pure1.pure1_pods" in task]

        assert module_tasks[0]["everpure.pure1.pure1_pods"] == {
            "app_id": "pure1:apikey:dssf2331sd",
            "key_file": "/home/user/private.pem",
        }
        for task in module_tasks:
            assert "register" not in task["everpure.pure1.pure1_pods"]
            later = json.dumps(tasks[tasks.index(task) + 1 :])
            assert "{{ %s['pure1_pods']" % task["register"] in later

    def test_examples_read_only_what_an_earlier_task_registered(self):
        registered = set()
        for task in yaml.safe_load(pure1_pods.EXAMPLES):
            known = registered | ({"item"} if "loop" in task else set())
            read = set(re.findall(r"\{\{\s*(\w+)", json.dumps(task)))
            assert read <= known, task["name"]
            if "register" in task:
                registered.add(task["register"])

    def test_examples_name_every_module_in_full(self):
        for task in yaml.safe_load(pure1_pods.EXAMPLES):
            (action,) = set(task) - {"name", "register", "loop"}
            assert action.count(".") == 2, task["name"]

    def test_module_has_no_ansible_metadata(self):
        assert not hasattr(pure1_pods, "ANSIBLE_METADATA")
