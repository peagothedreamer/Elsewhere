# PROJECT_STATE

## 当前里程碑
macOS 适配（依据 `iOSRealRun-cli-18-macOS适配报告.md` 的落地清单）

- **M1 完成**：平台用户目录、启动向导文案、字体回退、`.icns` 图标。
- **M2 完成**：PyInstaller `BUNDLE` 产出 `.app`，PyInstaller 自带 ad-hoc 签名。
- **M3 部分完成**：CI 增加 `macos-15` 任务并产出 `Elsewhere-<ver>-arm64.dmg`；更新器支持 macOS DMG 就地替换。Developer ID 签名与公证未做。

## 已定决策
- macOS 上**普通用户运行，不提权**：注入走 `PreferredRsdTunnel` 的 userspace（PyTCP）路径，必要时回退 native tunnel；不引入 `sudo`/helper。
- 不照抄 iOSRealRun-cli-18 的 kernel `utun` 方案，注入层 `src/inject.py` 零改动。
- 更新包命名固定为 `-portable.zip`（Windows）与 `-arm64.dmg`（macOS），`updater.pick_asset()` 按后缀匹配。
- 用户数据目录：Windows `%APPDATA%\Elsewhere`，macOS `~/Library/Application Support/Elsewhere`。
- 自动更新失败必须**可见**：脚本写 `%TEMP%\Elsewhere-update.log`、用握手文件确认脚本已启动，失败弹窗；更新期间用置顶小窗覆盖「应用已关、新版未起」的空白期。
- 更新检查不走 `api.github.com`（未认证每 IP 60 次/小时，共享出口必然 403）：改为请求 `releases/latest`，读 302 后的 tag，按固定命名拼安装包地址。
- 打包只裁非运行必需件：QML 运行时 DLL、未导入的 PySide6 绑定、`opengl32sw.dll`、Qt 的 OpenSSL 后端、多余插件、chardet；Inno 用 `lzma2/ultra64`。`Qt6WebEngineCore` 无 debug 段，不可再裁，占安装包一半以上。

## 下一步
1. macOS 真机跑报告 §7 冒烟清单（重点：免 root 隧道日志、DDI 首次联网挂载、退出无残留、`.app` 启动与地图）。
   `build-macos` 已加 `Smoke test (.app)` 步骤（启动、用户目录、`pmd_pytcp` 是否进冻结包、启动报错），覆盖 §7 第 1/10 条中可自动化的部分；第 2–9 条需 Apple Silicon 真机 + iPhone。
2. 冻结版若报 `ModuleNotFoundError: pmd_pytcp`，在 spec 里加 `collect_submodules('pmd_pytcp')`。
3. 正式分发前补 Developer ID 签名 + `notarytool` 公证。

## 风险
- macOS 端到端行为尚未真机验证（报告基于依赖源码推断）。
- 未签名 `.app` 会被 Gatekeeper 拦截，README 已给右键打开/`xattr` 方案。
- iOS 17.0–17.3 的 native tunnel 回退与系统 `remoted` 可能互踢（pymobiledevice3 #1994）。
- 已删 `opengl32sw.dll`：没有 D3D11 的环境（极老显卡/部分虚拟机）下地图可能空白；需要兜底就把它加回 spec。
