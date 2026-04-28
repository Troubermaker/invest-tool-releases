#!/usr/bin/env python3
"""
invest_tool 发布脚本（包装 build.py）。

它做什么：
    1. 读 / 升 version.py 中的版本号
    2. 调 build.py 完整构建（Cython 保护 + 前端 + PyInstaller）
    3. dist/invest_tool/ 打成 release/invest_tool_vX.Y.Z_DATE.zip
    4. 生成 SHA256 校验和文件
    5. 从 git log 生成 RELEASE_NOTES_vX.Y.Z.txt

用法：
    python release.py                       使用当前版本号发布
    python release.py --bump patch          0.1.0 → 0.1.1
    python release.py --bump minor          0.1.0 → 0.2.0
    python release.py --bump major          0.1.0 → 1.0.0
    python release.py --skip-front          透传给 build.py
    python release.py --skip-cython         透传给 build.py（不推荐发布用）
    python release.py --no-build            跳过构建，仅基于现有 dist/ 重打包
    python release.py --git-tag             成功后自动 git tag vX.Y.Z（不 push）
    python release.py -h                    查看帮助

最终产物（在 release/ 目录）：
    invest_tool_v0.1.0_2026-04-27.zip
    invest_tool_v0.1.0_2026-04-27.zip.sha256
    invest_tool_v0.1.0_RELEASE_NOTES.txt
"""
import argparse
import datetime
import hashlib
import json as _json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


ROOT = Path(__file__).parent.resolve()
VERSION_FILE = ROOT / 'version.py'
DIST_BUNDLE = ROOT / 'dist' / 'invest_tool'
RELEASE_DIR = ROOT / 'release'
BUILD_PY    = ROOT / 'build.py'


# ---- 简易日志 ----
class _C:
    R='\033[91m'; G='\033[92m'; Y='\033[93m'; B='\033[94m'; DIM='\033[2m'; BOLD='\033[1m'; END='\033[0m'

def _enable_ansi():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

def step(m): print(f"\n{_C.BOLD}{_C.B}▶ {m}{_C.END}")
def ok(m):   print(f"  {_C.G}✓ {m}{_C.END}")
def warn(m): print(f"  {_C.Y}⚠ {m}{_C.END}")
def err(m):  print(f"  {_C.R}✗ {m}{_C.END}")
def dim(m):  print(f"  {_C.DIM}{m}{_C.END}")


# ---- 版本号读写 ----

def read_version():
    text = VERSION_FILE.read_text(encoding='utf-8')
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise RuntimeError(f"在 {VERSION_FILE.name} 里找不到 __version__")
    return m.group(1)

def write_version(new_v):
    text = VERSION_FILE.read_text(encoding='utf-8')
    new = re.sub(
        r'(__version__\s*=\s*)["\'][^"\']+["\']',
        f'\\1"{new_v}"',
        text, count=1
    )
    VERSION_FILE.write_text(new, encoding='utf-8')

def bump_version(level, current):
    parts = current.split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"当前版本号 {current} 不是 X.Y.Z 形式")
    major, minor, patch = map(int, parts)
    if level == 'major':
        return f"{major + 1}.0.0"
    if level == 'minor':
        return f"{major}.{minor + 1}.0"
    if level == 'patch':
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"未知 bump 级别: {level}")


# ---- 构建 ----

def run_build(skip_front, skip_cython):
    step("调用 build.py 完整构建")
    cmd = [sys.executable, str(BUILD_PY)]
    if skip_front:
        cmd.append('--skip-front')
    if skip_cython:
        cmd.append('--skip-cython')
    dim(f"$ {' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    if rc != 0:
        err(f"build.py 失败（退出码 {rc}）")
        return False
    if not DIST_BUNDLE.exists():
        err(f"未发现 {DIST_BUNDLE}，build.py 异常")
        return False
    ok("构建完成")
    return True


# ---- 打 zip ----

def make_zip(src_dir: Path, dest_zip: Path):
    step(f"打包 zip → {dest_zip.name}")
    if dest_zip.exists():
        dest_zip.unlink()
    total_files = 0
    total_bytes = 0
    t0 = time.time()
    with zipfile.ZipFile(dest_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(src_dir):
            for f in files:
                full = Path(root) / f
                # 内部路径保留 invest_tool/ 这一层，解压后是 invest_tool/ 目录
                arc = full.relative_to(src_dir.parent)
                z.write(full, arc)
                total_files += 1
                total_bytes += full.stat().st_size
    dt = time.time() - t0
    zip_size = dest_zip.stat().st_size
    ok(f"{total_files} 个文件，{total_bytes / 1024 / 1024:.1f} MB → "
       f"{zip_size / 1024 / 1024:.1f} MB（压缩率 {zip_size / total_bytes * 100:.0f}%，耗时 {dt:.1f}s）")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---- 发布说明 ----

def _git(*args) -> str:
    """跑 git 命令，强制 UTF-8 解码（中文 commit 不会炸 Windows 默认 GBK）。
    出错或非 0 返回空串。"""
    try:
        r = subprocess.run(
            ['git', *args], cwd=ROOT,
            capture_output=True, encoding='utf-8', errors='replace',
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()
    except Exception:
        pass
    return ''


def collect_recent_commits(n=20):
    """
    获取自上个 tag 起的提交，找不到 tag 就退化为最近 N 条。
    自动过滤噪音：merge commit、chore: bump version、空 subject。
    """
    last_tag = _git('describe', '--tags', '--abbrev=0')
    if last_tag:
        log = _git('log', f'{last_tag}..HEAD', '--pretty=format:%h|||%s')
        prefix = f"自 {last_tag} 起：\n"
    else:
        log = _git('log', f'-n{n}', '--pretty=format:%h|||%s')
        prefix = f"最近 {n} 条提交：\n"

    if not log:
        return "（无 git 记录）"

    # 过滤噪音
    NOISE_PATTERNS = [
        r'^Merge ',                  # merge commits
        r'^chore:?\s*bump',          # version bump
        r'^chore:?\s*release',       # release commits
        r'^WIP\b',                   # work-in-progress
        r'^wip\b',
        r'^\[skip ci\]',
    ]
    noise_re = re.compile('|'.join(NOISE_PATTERNS), re.IGNORECASE)

    lines = []
    for raw in log.splitlines():
        if '|||' not in raw:
            continue
        sha, subject = raw.split('|||', 1)
        subject = subject.strip()
        if not subject or noise_re.match(subject):
            continue
        lines.append(f"- {sha} {subject}")

    if not lines:
        return "（自上次发版以来无功能性提交）"
    return prefix + '\n'.join(lines)


def write_latest_json(version: str, zip_name: str, zip_path: Path, sha256: str, dest: Path):
    """生成给客户端 in-app updater 用的 latest.json。
    你需要把它（连同 zip）push 到公开 Gitee 发布仓库的根目录。"""
    step("生成 latest.json（在线更新清单）")

    # 从 update_config 取仓库信息拼下载 URL
    try:
        import update_config
        if update_config.is_configured():
            download_url = (
                f"https://gitee.com/{update_config.GITEE_USER}/"
                f"{update_config.RELEASE_REPO}/releases/download/v{version}/{zip_name}"
            )
        else:
            download_url = f"https://REPLACE_WITH_GITEE_URL/releases/download/v{version}/{zip_name}"
            warn("update_config.GITEE_USER 还是占位符，download_url 已用 REPLACE_WITH_GITEE_URL")
    except Exception:
        download_url = f"https://REPLACE_WITH_GITEE_URL/releases/download/v{version}/{zip_name}"

    manifest = {
        "version":                version,
        "release_date":           datetime.date.today().isoformat(),
        "min_compatible_version": "0.0.0",   # 早期不强更，留接口
        "download_url":           download_url,
        "sha256":                 sha256,
        "size_bytes":             zip_path.stat().st_size,
        "release_notes":          collect_recent_commits(),
    }
    import json as _json
    dest.write_text(_json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    ok(f"latest.json 已生成（push 到公开 Gitee 仓库根目录）")


def write_release_notes(version, zip_path: Path, sha256: str, dest: Path):
    step("生成发布说明")
    size_mb = zip_path.stat().st_size / 1024 / 1024
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    commits = collect_recent_commits()
    body = f"""=== invest_tool v{version} ===

打包时间: {timestamp}
压缩包:   {zip_path.name}
体积:     {size_mb:.1f} MB
SHA256:   {sha256}

=== 本版变更 ===
{commits}

=== 安装与运行 ===
1. 解压 zip 到任意目录（推荐路径不含中文 / 空格 / 特殊字符）
2. 进入 invest_tool/ 文件夹，双击 invest_tool.exe
3. 首次启动会显示激活页 —— 输入激活码后解锁全部功能
4. 你的自选 / 持仓 / 激活状态 / 偏好都保存在：
       %APPDATA%\\InvestTool\\invest_data.db
   （可在文件管理器地址栏粘贴 %APPDATA%\\InvestTool 直达）

=== 升级到新版本 ===
1. 直接下载新版 zip，解压覆盖原 invest_tool/ 文件夹即可
2. 数据保存在 %APPDATA%\\InvestTool\\，不在 install 目录里，**不会丢失**
3. 激活码也会自动保留

=== 系统要求 ===
- Windows 10 / 11（64 位）
- WebView2 运行时（Win10 1809+ 一般已自带；缺失时请装：
  https://developer.microsoft.com/microsoft-edge/webview2/）

=== 完整性校验 ===
解压前可对比 SHA256 确保下载未损坏：
    PowerShell：Get-FileHash {zip_path.name} -Algorithm SHA256

=== 说明 ===
本工具数据来自第三方公开网站，仅做格式化展示，不做投资建议。
盈亏请基于交易所原始数据，本工具数据仅供复盘参考。
"""
    dest.write_text(body, encoding='utf-8')
    ok(f"发布说明：{dest.name}")


# ---- Gitee Releases API（创建 release + 上传 zip）----

def _get_gitee_token():
    """优先环境变量 GITEE_TOKEN；fallback release_secret.py。"""
    env_token = os.environ.get('GITEE_TOKEN', '').strip()
    if env_token:
        return env_token
    try:
        import release_secret
        token = getattr(release_secret, 'GITEE_TOKEN', '').strip()
        if token and not token.startswith('REPLACE_'):
            return token
    except ImportError:
        pass
    return None


def upload_zip_to_gitee_release(version: str, zip_path: Path, sha256: str, release_notes: str):
    """
    用 Gitee Releases API 创建 release + 上传 zip 附件。
    需要 PAT（personal access token）—— 见 release_secret.py.example。

    成功返回 True；token 未配置 / 网络失败 / API 报错 都返回 False，
    调用方继续走"手动上传"提示。
    """
    step("自动上传 zip 到 Gitee Releases（API）")

    token = _get_gitee_token()
    if not token:
        warn("未找到 Gitee token —— 跳过自动上传")
        warn("如要启用：cp release_secret.py.example release_secret.py，填入 PAT")
        return False

    try:
        import update_config
    except ImportError:
        err("update_config 未加载，跳过")
        return False

    owner = getattr(update_config, 'GITEE_USER', '')
    repo  = getattr(update_config, 'RELEASE_REPO', '')
    branch = getattr(update_config, 'RELEASE_BRANCH', 'master')
    if not update_config.is_configured() or not owner or not repo:
        err("update_config.GITEE_USER / RELEASE_REPO 未正确配置，跳过")
        return False

    # 1. 先 query 这个 tag 是否已经存在 release（避免冲突）
    existing = _gitee_get_release_by_tag(owner, repo, version, token)
    if existing:
        warn(f"Gitee 上 v{version} release 已存在（id={existing.get('id')}），跳过创建")
        warn(f"如要覆盖：先到网页删除该 release，再重跑 release.py --publish")
        return False

    # 2. 创建 release
    release_id = _gitee_create_release(owner, repo, version, branch, release_notes, token)
    if not release_id:
        return False
    ok(f"已创建 release v{version}（id={release_id}）")

    # 3. 上传 zip 附件
    if not _gitee_upload_attachment(owner, repo, release_id, zip_path, token):
        err("zip 上传失败，但 release 已创建。请手动到网页补传。")
        return False
    ok(f"已上传 {zip_path.name}（{zip_path.stat().st_size / 1024 / 1024:.1f} MB）")
    return True


def _gitee_get_release_by_tag(owner, repo, version, token):
    """根据 tag 查 release。404 返回 None。"""
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases/tags/v{version}?access_token={token}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        warn(f"查询 release 异常 {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        return None
    except Exception as e:
        warn(f"查询 release 网络异常: {e}")
        return None


def _gitee_create_release(owner, repo, version, branch, body, token):
    """创建 release，返回 release_id；失败返回 None。"""
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases"
    payload = {
        'access_token':     token,
        'tag_name':         f'v{version}',
        'name':             f'v{version}',
        'body':             body or f'Release v{version}',
        'target_commitish': branch,
    }
    data = _json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            obj = _json.loads(resp.read())
            return obj.get('id')
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode('utf-8', errors='replace')[:400]
        err(f"创建 release 失败 HTTP {e.code}: {body_txt}")
        return None
    except Exception as e:
        err(f"创建 release 网络异常: {e}")
        return None


def _gitee_upload_attachment(owner, repo, release_id, file_path: Path, token):
    """上传文件到 release 的 attach_files。multipart/form-data 手撸（不引 requests 依赖）。"""
    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases/{release_id}/attach_files?access_token={token}"

    # 手写 multipart body
    boundary = f"----InvestToolBoundary{int(time.time())}"
    crlf = b'\r\n'
    file_bytes = file_path.read_bytes()
    body_parts = [
        f'--{boundary}'.encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode(),
        b'Content-Type: application/zip',
        b'',
        file_bytes,
        f'--{boundary}--'.encode(),
        b'',
    ]
    body = crlf.join(body_parts)

    req = urllib.request.Request(
        url, data=body,
        headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body)),
        },
        method='POST',
    )
    try:
        # 上传可能耗时几十秒（25MB），超时给宽
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode('utf-8', errors='replace')[:400]
        err(f"上传附件失败 HTTP {e.code}: {body_txt}")
        return False
    except Exception as e:
        err(f"上传附件网络异常: {e}")
        return False


# ---- Git tag ----

def make_git_tag(version):
    step(f"创建 git tag v{version}")
    rc = subprocess.run(['git', 'tag', f'v{version}'], cwd=ROOT).returncode
    if rc == 0:
        ok(f"已创建 tag v{version}（未推送，需手动 git push origin v{version}）")
    else:
        warn(f"tag 创建失败（可能已存在），跳过")


# ---- 自动 push latest.json 到公开发布仓库 ----

def publish_manifest_to_release_repo(version: str, latest_json: Path):
    """
    把 release/latest.json 复制到公开发布仓库的本地 clone 目录，
    然后 git pull → copy → add → commit → push 一条龙。

    zip 包仍然需要你手动到 Gitee 网页上传到 Releases。
    """
    step("自动 push latest.json 到公开发布仓库")

    try:
        import update_config
    except ImportError:
        err("update_config 模块加载失败，跳过 publish")
        return False

    repo_path = getattr(update_config, 'RELEASE_REPO_LOCAL_PATH', None)
    if not repo_path:
        err("update_config.RELEASE_REPO_LOCAL_PATH 未设置，跳过 publish")
        err("请打开 update_config.py 把公开仓库的本地 clone 路径填进去")
        return False

    repo = Path(repo_path)
    if not repo.exists():
        err(f"路径不存在：{repo}")
        err("请确认你已经 clone 公开发布仓库到这个位置：")
        err(f"  git clone https://gitee.com/{getattr(update_config, 'GITEE_USER', '')}/"
            f"{getattr(update_config, 'RELEASE_REPO', '')}.git {repo}")
        return False
    if not (repo / '.git').exists():
        err(f"{repo} 不是 git 仓库")
        return False
    if not latest_json.exists():
        err(f"找不到 {latest_json}，跳过")
        return False

    # 1. git pull（防冲突）
    dim(f"$ cd {repo} && git pull --ff-only")
    rc = subprocess.run(['git', 'pull', '--ff-only'], cwd=repo).returncode
    if rc != 0:
        err("git pull 失败 —— 仓库可能有未提交改动或冲突，请人工处理后再试")
        return False

    # 2. 复制 latest.json
    dest = repo / 'latest.json'
    shutil.copy2(latest_json, dest)
    ok(f"latest.json → {dest}")

    # 3. 检查是否真的有改动（避免空提交）
    diff = subprocess.run(
        ['git', 'diff', '--quiet', 'latest.json'],
        cwd=repo,
    ).returncode
    if diff == 0:
        warn("latest.json 内容跟仓库里的一致，跳过 commit / push")
        return True

    # 4. add + commit + push
    rc = subprocess.run(['git', 'add', 'latest.json'], cwd=repo).returncode
    if rc != 0:
        err("git add 失败")
        return False

    rc = subprocess.run(
        ['git', 'commit', '-m', f'release v{version}'],
        cwd=repo,
    ).returncode
    if rc != 0:
        err("git commit 失败")
        return False

    rc = subprocess.run(['git', 'push'], cwd=repo).returncode
    if rc != 0:
        err("git push 失败 —— 检查网络 / Gitee 凭证")
        return False

    ok(f"latest.json 已 push（commit: release v{version}）")
    return True


# ---- main ----

def main():
    parser = argparse.ArgumentParser(
        description='invest_tool 发布脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n  python release.py --bump patch --git-tag\n  python release.py --skip-front',
    )
    parser.add_argument('--bump', choices=['patch', 'minor', 'major'],
                        help='升版后再打包')
    parser.add_argument('--skip-front', action='store_true', help='透传 build.py')
    parser.add_argument('--skip-cython', action='store_true',
                        help='透传 build.py（不推荐发布用）')
    parser.add_argument('--no-build', action='store_true',
                        help='跳过构建，仅基于现有 dist/ 重打 zip')
    parser.add_argument('--git-tag', action='store_true',
                        help='成功后自动创建 git tag（不 push）')
    parser.add_argument('--publish', action='store_true',
                        help='自动把 latest.json push 到公开发布仓库（路径见 update_config.py）')
    args = parser.parse_args()

    _enable_ansi()
    print(f"{_C.BOLD}=== invest_tool 发布脚本 ==={_C.END}")
    t_total = time.time()

    # 1. 版本号
    step("解析版本号")
    current = read_version()
    if args.bump:
        new = bump_version(args.bump, current)
        write_version(new)
        ok(f"升版：{current} → {new}")
        version = new
    else:
        ok(f"使用当前版本：{current}")
        version = current

    # 2. 构建
    if args.no_build:
        step("跳过构建（--no-build）")
        if not DIST_BUNDLE.exists():
            err(f"--no-build 要求 {DIST_BUNDLE} 已存在")
            sys.exit(1)
        warn("注意：dist/ 是上次构建的产物，可能与当前源码不一致")
    else:
        if not run_build(args.skip_front, args.skip_cython):
            sys.exit(1)

    # 3. 准备 release/ 目录
    RELEASE_DIR.mkdir(exist_ok=True)
    today = datetime.date.today().isoformat()
    zip_name  = f"invest_tool_v{version}_{today}.zip"
    zip_path  = RELEASE_DIR / zip_name
    sha_path  = RELEASE_DIR / (zip_name + '.sha256')
    notes_path = RELEASE_DIR / f"invest_tool_v{version}_RELEASE_NOTES.txt"

    # 4. 打 zip
    make_zip(DIST_BUNDLE, zip_path)

    # 5. SHA256
    step("计算 SHA256")
    digest = sha256_file(zip_path)
    sha_path.write_text(f"{digest}  {zip_name}\n", encoding='utf-8')
    ok(f"SHA256: {digest[:16]}...{digest[-16:]}")

    # 6. 发布说明
    write_release_notes(version, zip_path, digest, notes_path)

    # 7. 自动生成 latest.json（你 push 到公开 Gitee 仓库后，用户 app 会读这个）
    latest_json_path = RELEASE_DIR / 'latest.json'
    write_latest_json(version, zip_name, zip_path, digest, latest_json_path)

    # 8. 可选：自动 push latest.json + 上传 zip 到 Gitee Releases
    publish_ok = True
    upload_ok = False
    if args.publish:
        publish_ok = publish_manifest_to_release_repo(version, latest_json_path)
        # 上传 zip 是独立步骤——可能 publish 成功但 upload 失败，或 upload 跳过
        upload_ok = upload_zip_to_gitee_release(
            version=version,
            zip_path=zip_path,
            sha256=digest,
            release_notes=collect_recent_commits(),
        )

    # 9. 可选 git tag
    if args.git_tag:
        make_git_tag(version)

    # 总结
    step("完成")
    dim(f"总耗时 {time.time() - t_total:.1f}s")
    print(f"\n{_C.BOLD}{_C.G}发布产物（{RELEASE_DIR.relative_to(ROOT).as_posix()}/）：{_C.END}")
    print(f"  • {zip_path.name}              ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  • {sha_path.name}")
    print(f"  • {notes_path.name}")
    print(f"  • latest.json")

    if args.publish:
        manifest_status = '✓' if publish_ok else '✗'
        upload_status   = '✓' if upload_ok   else '✗'
        print(f"\n{_C.BOLD}{_C.G}发布动作：{_C.END}")
        print(f"  {manifest_status} latest.json push 到公开仓库")
        print(f"  {upload_status} zip 上传到 Gitee Releases（API）")

        if publish_ok and upload_ok:
            print(f"\n{_C.BOLD}{_C.G}🎉 全自动发布完成！用户 app 下次启动即检测到 v{version}{_C.END}")
        elif publish_ok and not upload_ok:
            uc = __import__('update_config')
            print(f"\n{_C.BOLD}{_C.Y}zip 没自动传上去，请手动补传：{_C.END}")
            print(f"  打开 https://gitee.com/{uc.GITEE_USER}/{uc.RELEASE_REPO}/releases")
            print(f"  → 创建发布版本 → tag v{version} → 上传 {zip_name}")
            print(f"  （或配置 release_secret.py 启用自动上传，参考 release_secret.py.example）")
        else:
            print(f"\n{_C.R}⚠ --publish 部分失败，详见上方错误日志{_C.END}")
    else:
        print(f"\n{_C.BOLD}{_C.Y}手动发布步骤（或下次加 --publish 一键自动）：{_C.END}")
        print(f"  1. 公开仓库 push latest.json：")
        print(f"     cd <公开仓库本地路径>")
        print(f"     git pull && cp <项目>\\release\\latest.json . && git add latest.json")
        print(f"     git commit -m 'release v{version}' && git push")
        print(f"  2. Gitee 网页上传 {zip_name} 到该仓库的 Releases（tag v{version}）")
        print(f"  3. 用户 app 下次启动即检测到新版")

    if not args.git_tag:
        print(f"\n{_C.DIM}提示：用 --git-tag 自动给主源码仓库打 tag v{version}{_C.END}")


if __name__ == '__main__':
    main()
