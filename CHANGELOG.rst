============================
Everpure.Pure1 Release Notes
============================

.. contents:: Topics

v1.5.1
======

Bugfixes
--------

- all modules - a failure to create the Pure1 client or to reach Pure1 now reports its cause instead of a generic message.
- all modules - the missing library message now names only ``py-pure-client``.
- pure1_array_tags - Fix creating and updating tags with py-pure-client 1.70.0 and later.
- pure1_array_tags - Include the Pure1 error context in failure messages.
- pure1_array_tags - Report an unknown array name with the Pure1 error message instead of a traceback.
- pure1_info - A 403 answer returns that subset empty with a warning.
- pure1_info - An API error now fails with the API message instead of a traceback.
- pure1_info - Fixed ``AttributeError`` on ``get_array_tags`` in the ``appliances`` and ``all`` subsets.
- pure1_info - Fixed bandwidth values of the ``appliances`` subset, which were 100 times too small.
- pure1_info - Fixed the ``appliances`` subset failing for arrays with tags, an unknown operating system or a failed metric call.
- pure1_info - Fixed the ``invoices`` subset failing on invoices with unset fields or line components.
- pure1_info - Fixed the ``subscriptions`` subset failing for organizations with a subscription license.
- pure1_info - License ``location`` now comes from the license site address and ``energy_usage`` is always null.
- pure1_info - Subscription ``org_name`` and invoice ``date`` and ``end_user_name`` are no longer always null.
- pure1_pods - A stretched pod is now listed under each of its arrays, or only under the requested array when ``array`` is set.

v1.5.0
======

Minor Changes
-------------

- Add a pytest-based unit-test suite covering all modules, wired into CI with coverage reporting.

Bugfixes
--------

- pure1_array_tags - Fix tag create/update failure handling, which raised an internal TypeError instead of returning a clean error message.
- pure1_info - Fix subscription license resource details being collected against the wrong object.
- pure1_info - Fix the ``invoices`` gather_subset, which could not be collected due to several coding errors.

New Modules
-----------

- everpure.pure1.pure1_drives - Collect array drives information from Pure1

v1.4.0
======

Minor Changes
-------------

- pure1_info - Added invoices subset

v1.3.0
======

Minor Changes
-------------

- Fized issue with use of environmental variables for key, key file and password
- password parameter is now optional, to allow for unprotected key files
- pure1_info.py - Added more subscrition detail

New Modules
-----------

- everpure.pure1.pure1_nics - Collect network interface information from Pure1
- everpure.pure1.pure1_pods - Collect FlashArray pod information from Pure1
- everpure.pure1.pure1_ports - Collect FlashArray port information from Pure1
- everpure.pure1.pure1_volumes - Collect volumes information from Pure1

v1.2.0
======

Minor Changes
-------------

- pure1_info - Add environmental subset for ESG data and platform insights

v1.1.0
======

Minor Changes
-------------

- All - Update documentation to use FQCNs
- pure1_info - Add array performance data and load (FA only). Add support contract status.

New Modules
-----------

- everpure.pure1.pure1_alerts - Collect array alerts from Pure1 based on severity
- everpure.pure1.pure1_network_interfaces - Collect array netowrk interface details from Pure1

v1.0.0
======

Major Changes
-------------

- pure1_array_tags - New module to manage Pure1 array tags
- pure1_info - New module for gathering Pure1 information
