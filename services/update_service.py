"""
在线更新服务（半自动 = "Level B"）。

完整流程：
    1. check_update()  → 拉 Gitee 上的 latest.json，比较 version.__version__
    2. start_download() → 后台线程下载 zip 到 %TEMP%\\InvestTool_Update\\
       前端轮询 get_progress() 看进度
    3. apply_update()   → 写 updater.bat 到 %TEMP%，启动 bat，本进程退出
       bat 等当前 exe 退出 → 备份当前 install → 解压新 zip 覆盖 → 启动新 exe → 清理

只在打包态 (sys.frozen) 真正执行 apply_update；开发态 apply 会拒绝执行
（避免误把 dist 替换掉）。
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import update_config
from version import __version__

logger = logging.getLogger(__name__)


# ---------------- 全局状态（线程安全）---------------- #

_lock = threading.Lock()
_state = {
    'phase':            'idle',  # idle | checking | downloading | verifying | ready | error
    'downloaded_bytes': 0,
    'total_bytes':      0,
    'error':            None,
    'zip_path':         None,
    'expected_sha256':  None,
}


def _set_state(**kwargs):
    with _lock:
        _state.update(kwargs)


def _get_state():
    with _lock:
        return dict(_state)


# ---------------- 公开 API ---------------- #

def check_update(timeout=8):
    """
    联网拉 latest.json 比较版本。返回：
        {
            "current_version":  "0.1.0",
            "latest_version":   "0.1.1" 或 None,
            "has_update":       bool,
            "force_update":     bool,           # 当前版本低于 min_compatible_version
            "release_notes":    str,
            "download_url":     str,
            "sha256":           str,
            "size_bytes":       int,
            "release_date":     str,
            "configured":       bool,           # update_config.GITEE_USER 是否已设置
            "error":            str | None,
        }
    """
    base = {
        "current_version": __version__,
        "latest_version":  None,
        "has_update":      False,
        "force_update":    False,
        "release_notes":   "",
        "download_url":    "",
        "sha256":          "",
        "size_bytes":      0,
        "release_date":    "",
        "configured":      update_config.is_configured(),
        "error":           None,
    }

    if not update_config.is_configured():
        base["error"] = "未配置 update_config.GITEE_USER"
        return base

    url = update_config.latest_json_url()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': f'invest-tool/{__version__}'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            manifest = json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        # 网络错误 / 仓库不存在 / latest.json 还没创建 都走这里 — 不视为故障
        logger.info(f"检查更新失败（无网络或 latest.json 不存在）: {e}")
        base["error"] = str(e)
        return base

    latest = str(manifest.get('version') or '')
    if not latest:
        base["error"] = "latest.json 里没有 version 字段"
        return base

    base["latest_version"]  = latest
    base["release_notes"]   = manifest.get('release_notes', '')
    base["download_url"]    = manifest.get('download_url', '')
    base["sha256"]          = manifest.get('sha256', '').lower()
    base["size_bytes"]      = int(manifest.get('size_bytes') or 0)
    base["release_date"]    = manifest.get('release_date', '')

    base["has_update"]      = _is_newer(latest, __version__)
    min_compat = manifest.get('min_compatible_version') or ''
    base["force_update"]    = bool(min_compat and _is_newer(min_compat, __version__))
    return base


def start_download(download_url: str, expected_sha256: str, total_bytes: int = 0):
    """
    后台启动下载。返回 True 表示已启动；同时只能跑一个下载。
    前端用 get_progress() 轮询进度。
    """
    cur = _get_state()
    if cur['phase'] in ('downloading', 'verifying'):
        return False

    _set_state(
        phase='downloading',
        downloaded_bytes=0,
        total_bytes=total_bytes,
        error=None,
        zip_path=None,
        expected_sha256=(expected_sha256 or '').lower(),
    )

    t = threading.Thread(
        target=_download_worker,
        args=(download_url, expected_sha256),
        daemon=True,
    )
    t.start()
    return True


def get_progress():
    """前端轮询用。返回当前下载状态。"""
    return _get_state()


def cancel_download():
    """取消下载（仅清状态，正在跑的线程会自然结束）。"""
    _set_state(phase='idle', downloaded_bytes=0, total_bytes=0,
               error=None, zip_path=None)


def apply_update():
    """
    把状态机推进到"应用更新"——写 updater.bat、启动它、然后 sys.exit。
    返回的字典只在 apply 失败时返回（成功的话进程已经退出）。
    """
    cur = _get_state()
    if cur['phase'] != 'ready' or not cur['zip_path'] or not os.path.exists(cur['zip_path']):
        return {'ok': False, 'error': '下载未就绪'}

    if not getattr(sys, 'frozen', False):
        return {'ok': False, 'error': '开发态不允许执行更新（避免误覆盖 dist）'}

    install_dir = os.path.dirname(sys.executable)  # dist/invest_tool/
    exe_name    = os.path.basename(sys.executable)  # invest_tool.exe

    bat_path = _write_updater_bat(
        install_dir=install_dir,
        exe_name=exe_name,
        zip_path=cur['zip_path'],
        wait_pid=os.getpid(),
    )

    # 启动 bat —— 用 CREATE_NEW_CONSOLE 而不是 DETACHED_PROCESS：
    # 1) 用户能看到一个标题为 "InvestTool 更新中" 的 console 窗口，知道在做事
    # 2) timeout / start / tar 等命令需要 console，DETACHED 会让 timeout 立即失败
    # 3) 出错时 console 会保留 + pause，方便排查
    try:
        CREATE_NEW_CONSOLE = 0x00000010
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            ['cmd', '/c', bat_path],
            creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as e:
        return {'ok': False, 'error': f'启动 updater 失败: {e}'}

    # 给 bat 200ms 时间稳定下来再退出，避免父进程退太快导致 bat 没起起来
    threading.Timer(0.2, _quit_app).start()
    return {'ok': True}


# ---------------- 内部 ---------------- #

def _is_newer(a: str, b: str) -> bool:
    """SemVer 比较。a > b 返回 True。"""
    def parse(v):
        try:
            return tuple(int(x) for x in v.split('.'))
        except ValueError:
            return (0,)
    return parse(a) > parse(b)


def _download_worker(url: str, expected_sha256: str):
    """后台下载 + SHA256 校验。"""
    tmp_dir = Path(tempfile.gettempdir()) / 'InvestTool_Update'
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 下载文件名直接用 URL 末段（带版本号），便于排查
    out_name = url.rsplit('/', 1)[-1] or 'update.zip'
    out_path = tmp_dir / out_name

    try:
        req = urllib.request.Request(url, headers={'User-Agent': f'invest-tool/{__version__}'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get('Content-Length', 0)) or _get_state()['total_bytes']
            _set_state(total_bytes=total)

            sha = hashlib.sha256()
            chunk_size = 64 * 1024
            downloaded = 0
            with open(out_path, 'wb') as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    _set_state(downloaded_bytes=downloaded)
    except Exception as e:
        logger.warning(f"下载失败: {e}")
        _set_state(phase='error', error=f'下载失败: {e}')
        try:
            out_path.unlink()
        except OSError:
            pass
        return

    # 校验 SHA256
    _set_state(phase='verifying')
    actual = sha.hexdigest().lower()
    if expected_sha256 and actual != expected_sha256.lower():
        msg = f'SHA256 校验失败：期望 {expected_sha256[:16]}... 实得 {actual[:16]}...'
        logger.warning(msg)
        _set_state(phase='error', error=msg)
        try:
            out_path.unlink()
        except OSError:
            pass
        return

    _set_state(phase='ready', zip_path=str(out_path))


# ---------------- updater.bat ---------------- #

# Windows 批处理：等父进程退 → 备份 → 解压新 zip 覆盖 → 启动新 exe → 清理
# tar 是 Win10 1803+ 自带的解压工具；不要用 PowerShell Expand-Archive（慢且对长路径不稳）
#
# 改进点：
#   - title 让用户在任务栏看得到这个 console
#   - 全程 echo 重要状态 + 写 log 文件方便事后排查
#   - 出错 pause，console 不会一闪而过
#   - 成功才删 bat 自身，失败保留 + 提示用户截图
_UPDATER_BAT = r"""@echo off
chcp 65001 >nul
title InvestTool 更新中（请勿关闭此窗口）
setlocal enabledelayedexpansion

set "EXE_NAME={exe_name}"
set "INSTALL_DIR={install_dir}"
set "ZIP_FILE={zip_file}"
set "WAIT_PID={wait_pid}"
set "BACKUP_DIR=%INSTALL_DIR%.bak"
set "LOG_FILE=%~dp0updater.log"

echo. > "%LOG_FILE%"
echo [%date% %time%] InvestTool Updater started >> "%LOG_FILE%"
echo   EXE_NAME    = %EXE_NAME% >> "%LOG_FILE%"
echo   INSTALL_DIR = %INSTALL_DIR% >> "%LOG_FILE%"
echo   ZIP_FILE    = %ZIP_FILE% >> "%LOG_FILE%"
echo   WAIT_PID    = %WAIT_PID% >> "%LOG_FILE%"

echo.
echo ======================================
echo  InvestTool 自动更新
echo ======================================
echo.
echo [步骤 1/4] 等待旧版本进程退出 (PID %WAIT_PID%)...

:wait_loop
tasklist /FI "PID eq %WAIT_PID%" 2>nul | find /I "%EXE_NAME%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo   旧进程已退出 >> "%LOG_FILE%"

:: 多等 2 秒确保文件句柄完全释放（防止 Windows 还没完全清理）
timeout /t 2 /nobreak >nul

echo [步骤 2/4] 备份当前版本到 .bak 目录...
if exist "%BACKUP_DIR%" rmdir /S /Q "%BACKUP_DIR%"
xcopy "%INSTALL_DIR%" "%BACKUP_DIR%" /E /I /Q /Y >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo   X 备份失败 >> "%LOG_FILE%"
    echo.
    echo [错误] 备份失败！未做任何改动。
    echo 详情见日志：%LOG_FILE%
    echo.
    pause
    exit /b 1
)
echo   备份完成 >> "%LOG_FILE%"

echo [步骤 3/4] 解压新版本...
for %%I in ("%INSTALL_DIR%") do set "PARENT=%%~dpI"
if "!PARENT:~-1!"=="\" set "PARENT=!PARENT:~0,-1!"

:: 删除旧的（再解压新的）— 避免老文件残留
rmdir /S /Q "%INSTALL_DIR%" 2>nul

tar -xf "%ZIP_FILE%" -C "%PARENT%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo   X 解压失败，回滚备份 >> "%LOG_FILE%"
    echo.
    echo [错误] 解压失败！正在从备份恢复...
    rmdir /S /Q "%INSTALL_DIR%" 2>nul
    move "%BACKUP_DIR%" "%INSTALL_DIR%" >nul
    echo 已回滚到旧版本，可正常使用。
    echo 详情见日志：%LOG_FILE%
    echo.
    pause
    exit /b 1
)
echo   解压完成 >> "%LOG_FILE%"

echo [步骤 4/4] 启动新版本...
echo   启动 %INSTALL_DIR%\%EXE_NAME% >> "%LOG_FILE%"
start "" "%INSTALL_DIR%\%EXE_NAME%"

:: 成功 → 清理 + 关窗口
rmdir /S /Q "%BACKUP_DIR%" 2>nul
del "%ZIP_FILE%" 2>nul

:: 成功才自删；失败保留方便排查
(goto) 2>nul & del "%~f0"
exit /b 0
"""


def _write_updater_bat(install_dir: str, exe_name: str, zip_path: str, wait_pid: int) -> str:
    """生成 updater.bat 写到 %TEMP%，返回路径。"""
    tmp_dir = Path(tempfile.gettempdir()) / 'InvestTool_Update'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    bat_path = tmp_dir / 'updater.bat'
    body = _UPDATER_BAT.format(
        exe_name=exe_name,
        install_dir=install_dir,
        zip_file=zip_path,
        wait_pid=wait_pid,
    )
    bat_path.write_text(body, encoding='utf-8')
    return str(bat_path)


def _quit_app():
    """优雅退出：先关 webview 窗口，关不掉就硬退。"""
    try:
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass
    except Exception:
        pass
    # 最后兜底
    os._exit(0)
