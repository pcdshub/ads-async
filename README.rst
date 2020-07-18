===============================
ads-async
===============================

.. image:: https://img.shields.io/travis/pcdshub/ads-async.svg
        :target: https://travis-ci.org/pcdshub/ads-async

.. image:: https://img.shields.io/pypi/v/ads-async.svg
        :target: https://pypi.python.org/pypi/ads-async


Asyncio (or sans-i/o) TwinCAT AMS/ADS testing server in pure Python.


Requirements
------------

* Python 3.6+
* (Optional) pytmc (for loading .tmc files in the server)


Server Functionality
--------------------

* Reference asyncio implementation.
* Loads .tmc files for symbol information (basic types only).
* Supports read, write, read/write of symbols (by handle or name).
* Supports 'sum up' bulk reads (by way of read_write).
* Pretends to create/delete notifications (not yet working)

Client Functionality
--------------------

* None

Installation
------------
::

  $ git clone git@github.com:pcdshub/ads-async
  $ cd ads-async
  $ pip install .

Running the Tests
-----------------
::

  $ pip install pytest
  $ pytest -vv ads_async/tests
