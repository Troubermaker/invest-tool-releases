"""
在线更新配置（Gitee 公开发布仓库的访问点）。

⚠️  发布前必须把 GITEE_USER 改成你自己的用户名！

为什么要"双仓库"？
    主源码仓库（你现在 git remote 那个）建议**保持私有**——里面有 SECRET、
    Cookies 等敏感信息。
    发布仓库**必须公开**——app 要在不带任何 token 的情况下能拉清单和下载 zip。
    安全交给"激活码 + Cython 编译"那一层负责（没付费的人下载到也用不了）。

如何设置：
    1. 去 https://gitee.com/projects/new 新建一个**公开**仓库
       推荐命名：invest-tool-releases
    2. 把下面的 GITEE_USER 改成你的用户名
    3. 每次 release.py 完后，手动把 zip 上传到 Releases，把 latest.json
       commit + push 到该仓库根目录
"""

# 你的 Gitee 用户名（必填）
GITEE_USER = "luckyforever666"

# 公开发布仓库名（默认 invest-tool-releases，按需改）
RELEASE_REPO = "invest-tool-releases"

# 仓库分支（一般是 master 或 main）
RELEASE_BRANCH = "master"

# 公开发布仓库在你电脑上的本地 clone 路径（用于 release.py --publish 自动 push latest.json）
# 留空 None / "" 则不自动 push，需要手动操作
# 例：r"D:\Project\invest-tool-releases"
RELEASE_REPO_LOCAL_PATH = r"D:\Project\invest-tool-releases"


def latest_json_url():
    """app 启动时拉这个 URL 检测新版本。"""
    return f"https://gitee.com/{GITEE_USER}/{RELEASE_REPO}/raw/{RELEASE_BRANCH}/latest.json"


def is_configured():
    """返回 True 表示已正确设置 GITEE_USER（不是占位符）。
    未配置时 update_service 会跳过更新检测，避免拿占位符 URL 打无效请求。"""
    return GITEE_USER and GITEE_USER != "YOUR_GITEE_USERNAME"
