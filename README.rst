==========================
 ReadMe for Mirezu Merger
==========================

*In other languages:* `简体中文 <README.zh-cn.rst>`__.


Introduction
------------

Mirezu Merger is a Python command line tool for merging Clash/Mihomo-style
YAML configuration files provided by multiple subscription services. The
project now offers two workflows: the more approachable ``wizard`` mode for
most users, and the original manual ``build`` mode for advanced users. The
former lets you provide subscription URLs, choose output targets and a
strategy preset, then generate ready-to-import configuration files; the
latter keeps the YAML template + TOML config + profile patch workflow for
users who want to maintain those assets themselves. Both workflows reuse the
same merge pipeline and can generate variants for different kinds of devices
(computers, smartphones, home routers, etc.)


Installation
------------

Using uv
^^^^^^^^

You could install Mirezu Merger on your system with `uv <https://docs.astral.sh/uv/>`__.
First, install uv.
Then, to install, run the following command::

  uv tool install git+https://github.com/rocky-star/mirezu-merger

You could also run Mirezu Merger directly without installation using `uvx`::

  uvx --from git+https://github.com/rocky-star/mirezu-merger mirezu-merger

Using pipx
^^^^^^^^^^

You could also install Mirezu Merger on your system with `pipx <https://pipx.pypa.io/stable/>`__.
First, install pipx.
Then, to install, run the following command::

  pipx install -e git+https://github.com/rocky-star/mirezu-merger

Using pip in a virtual environment (venv)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are unable or do not want to install uv or pipx, you could also create a virtual environment, then install Mirezu Merger inside it.
First, `create and activate a virtual environment <https://docs.python.org/3/library/venv.html>`__.
Then, to install, run the following command::

  pip install -e git+https://github.com/rocky-star/mirezu-merger


Usage
-----

Wizard mode
^^^^^^^^^^^

If you mainly want ready-to-import configuration files from a few
subscription URLs, use ``wizard``. The wizard walks you through entering
subscriptions, choosing output targets (``desktop``, ``mobile``, ``router``),
choosing a strategy preset (``general``, ``streaming``, ``ai``,
``minimal``), then configuring a few network options for fetching
subscriptions.

The simplest usage is::

  mirezu-merger wizard

You can also pass subscriptions directly on the command line, or load them
from a text file::

  mirezu-merger wizard --subscription https://example.com/sub-a.yaml --subscription https://example.com/sub-b.yaml
  mirezu-merger wizard --subscription-file subscriptions.txt

By default, the wizard writes results to ``output/``, including
``proxy-providers.yaml``, the selected target output files,
``wizard.session.toml``, and exported ``generated/`` assets for later reuse
or inspection.

If ``wizard.config.toml`` exists in the current directory and contains
``[analysis.ai]``, the wizard also asks whether to enable AI node analysis.
See ``wizard.config.toml.example`` in the repository.

To regenerate from the last saved wizard session, run::

  mirezu-merger wizard --resume

If the session file is not in the default location, or you want resumed
output in a different directory, pass ``--session-file`` and ``-o``
explicitly.

Manual mode
^^^^^^^^^^^

If you prefer maintaining your own YAML template, TOML config, and profile
patches, see the `User's Manual (Chinese edition only) <https://rocky-star.github.io/mirezu-merger/manual.zh-cn.pdf>`__.
The README intentionally keeps that advanced workflow in the manual instead
of repeating it here. The corresponding command is::

  mirezu-merger build config.toml profiles


License
-------

Mirezu Merger is licensed under `Apache License, Version 2.0  <https://www.apache.org/licenses/LICENSE-2.0>`__.
