====================
 Mirezu Merger 自述
====================

*其它语言版本：*\ `English <README.rst>`__


简介
----

Mirezu Merger 是一个用于合并由不同订阅提供的 Clash/Mihomo 风格 YAML
配置文件的 Python 命令行工具。项目目前提供两种工作流：对大多数用户更友好的
``wizard`` 向导模式，以及继续保留给高级用户的手动 ``build`` 模式。前者让您只需
提供订阅链接、选择输出目标和策略预设即可生成可直接导入的配置文件；后者则允许
您继续维护自己的 YAML 模板、TOML 配置和 profile patch。两种模式最终都会复用
同一套合并流程，并为不同种类的设备（电脑、手机、路由器等）生成不同的变体。


安装
----

用 uv 安装
^^^^^^^^^^

您可以用 `uv <https://docs.astral.sh/uv/>`__ 在您的系统上安装 Mirezu Merger。
首先，安装 uv。
然后运行如下命令以安装 Mirezu Merger::

  uv tool install git+https://github.com/rocky-star/mirezu-merger

您也可以使用 ``uvx`` 直接运行 Mirezu Merger::

  uvx --from git+https://github.com/rocky-star/mirezu-merger mirezu-merger

用 pipx 安装
^^^^^^^^^^^^

您也可以用 `pipx <https://pipx.pypa.io/stable/>`__ 在您的系统上安装 Mirezu Merger。
首先，安装 pipx。
然后运行如下命令以安装 Mirezu Merger::

  pipx install -e git+https://github.com/rocky-star/mirezu-merger

在虚拟环境内用 pip 安装
^^^^^^^^^^^^^^^^^^^^^^^

如果您无法或不愿意安装 uv 或 pipx，您也可以创建一个虚拟环境，并在虚拟环境内安装 Mirezu Merger。
创建虚拟环境帮助您的全局 Python 环境免受污染。
首先，\ `创建并启用一个虚拟环境 <https://docs.python.org/zh-cn/3/library/venv.html>`__\ 。
然后运行如下命令以安装 Mirezu Merger::

  pip install -e git+https://github.com/rocky-star/mirezu-merger


用法
----

向导模式
^^^^^^^^

如果您只是想把多条订阅快速整理成可直接导入的配置文件，推荐使用 ``wizard``
命令。向导模式会引导您录入订阅链接，选择输出目标（``desktop``、``mobile``、
``router``），选择策略预设（``general``、``streaming``、``ai``、
``minimal``），再配置抓取订阅时所需的少量网络选项并生成结果。

最简单的用法如下::

  mirezu-merger wizard

您也可以直接通过命令行参数传入订阅链接，或从文本文件中批量读取::

  mirezu-merger wizard --subscription https://example.com/sub-a.yaml --subscription https://example.com/sub-b.yaml
  mirezu-merger wizard --subscription-file subscriptions.txt

默认情况下，向导会把结果写入 ``output/``，其中包括 ``proxy-providers.yaml``、
所选目标对应的输出文件、``wizard.session.toml``，以及导出的 ``generated/``
内部资产，方便您后续复用或检查。

如果当前目录下存在 ``wizard.config.toml`` 且其中配置了 ``[analysis.ai]``，
向导还会额外询问是否启用 AI 节点分析。可参考仓库中的
``wizard.config.toml.example``。

如果您想基于上一次保存的向导会话重新生成，可运行::

  mirezu-merger wizard --resume

若会话文件不在默认位置，或您希望将恢复后的输出写到新的目录，请显式指定
``--session-file`` 和 ``-o``。

手动模式
^^^^^^^^

如果您打算自己维护 YAML 模板、TOML 配置和 profile patch，请直接阅读\
 `用户手册 <https://rocky-star.github.io/mirezu-merger/manual.zh-cn.pdf>`__\ 。
README 不重复展开这一套高级工作流。对应命令入口为::

  mirezu-merger build config.toml profiles


许可证
------

Mirezu Merger 使用 `Apache 许可协议，版本 2.0  <https://www.apache.org/licenses/LICENSE-2.0>`__\ 。
