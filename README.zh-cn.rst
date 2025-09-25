======================
 *Mirezu Merger* 自述
======================

*其它语言版本：*\ `English <README.rst>`__


简介
----

*Mirezu Merger* 是一种能帮助您合并由不同订阅提供的 YAML 配置文件的 Python 脚本。
例如，假设您订阅了两个或更多订阅服务，而这些订阅服务所提供的 YAML 配置文件各自包含了不同的节点、节点组和路由规则等，*Mirezu Merger* 能将这些配置文件合并到一起，并为不同种类的设备（电脑、手机、路由器等）提供不同的变体。


安装
----

用 uv 安装
^^^^^^^^^^

您可以用 `uv <https://docs.astral.sh/uv/>`__ 在您的系统上安装 *Mirezu Merger*。
首先，安装 uv。
然后运行如下命令以安装 *Mirezu Merger*::

  uv tool install git+https://github.com/rocky-star/mirezu-merger

您也可以使用 ``uvx`` 直接运行 *Mirezu Merger*::

  uvx --from git+https://github.com/rocky-star/mirezu-merger mirezu-merger

用 pipx 安装
^^^^^^^^^^^^

您也可以用 `pipx <https://pipx.pypa.io/stable/>`__ 在您的系统上安装 *Mirezu Merger*。
首先，安装 pipx。
然后运行如下命令以安装 *Mirezu Merger*::

  pipx install -e git+https://github.com/rocky-star/mirezu-merger

在虚拟环境内用 pip 安装
^^^^^^^^^^^^^^^^^^^^^^^

如果您无法或不愿意安装 uv 或 pipx，您也可以创建一个虚拟环境，并在虚拟环境内安装 *Mirezu Merger*。
创建虚拟环境帮助您的全局 Python 环境免受污染。
首先，\ `创建并启用一个虚拟环境 <https://docs.python.org/zh-cn/3/library/venv.html>`__\ 。
然后运行如下命令以安装 *Mirezu Merger*::

  pip install -e git+https://github.com/rocky-star/mirezu-merger


用法
----

.. attention::
   正在施工


许可证
------

*Mirezu Merger* 使用 `Apache 许可协议，版本 2.0  <https://www.apache.org/licenses/LICENSE-2.0>`__\ 。
