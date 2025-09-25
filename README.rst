============================
 ReadMe for *Mirezu Merger*
============================

*In other languages:* `简体中文 <README.zh-cn.rst>`__.


Introduction
------------

*Mirezu Script* is a Python script, helping you to merge YAML configuration files provided by subscription services.
For example, assuming you have ordered two or more subscription services, and each of configuration files provided by them has different sets of nodes, node groups, routing rules, *etc.*, *Mirezu Merger* is able to merge them, then generate different variants of merged configuration files for various kinds of devices (computers, smartphones, home routers, *etc.*).


Installation
------------

Using uv
--------

You could install *Mirezu Merger* on your system with `uv <https://docs.astral.sh/uv/>`__.
First, install uv.
Then, to install, run the following command::

  uv tool install git+https://github.com/rocky-star/mirezu-merger

You could also run *Mirezu Merger* directly without installation using `uvx`::

  uvx --from git+https://github.com/rocky-star/mirezu-merger mirezu-merger

Using pipx
----------

You could also install *Mirezu Merger* on your system with `pipx <https://pipx.pypa.io/stable/>`__.
First, install pipx.
Then, to install, run the following command::

  pipx install -e git+https://github.com/rocky-star/mirezu-merger

Using pip in a virtual environment (venv)
-----------------------------------------

If you are unable or do not want to install uv or pipx, you could also create a virtual environment, then install *Mirezu Merger* inside it.
First, `create and activate a virtual environment <https://docs.python.org/3/library/venv.html>`__.
Then, to install, run the following command::

  pip install -e git+https://github.com/rocky-star/mirezu-merger


Usage
-----

.. attention::
   UNDER CONSTRUCTION


License
-------

*Mirezu Merger* is licensed under `Apache License, Version 2.0  <https://www.apache.org/licenses/LICENSE-2.0>`__.
