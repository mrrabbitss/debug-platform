"""Prepare release assets only after the exact full EXE passes installation checks."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare(out):
    build = json.loads((out / "installer-build.json").read_text(encoding="utf-8"))
    package = json.loads((out / "package-verification.json").read_text(encoding="utf-8"))
    setup = json.loads((out / "setup-verification.json").read_text(encoding="utf-8"))
    assert package["status"] == setup["status"] == "PASS" and not package["source_dirty"]
    assert setup["installer_sha256"] == build["sha256"]
    stage = out / "github-release"
    stage.mkdir(exist_ok=True)
    client = ROOT / "artifacts/lan/expert-0.5.0/github-release/GWAP-Client-0.5.0.zip"
    shutil.copyfile(client, stage / client.name)
    shutil.copyfile(ROOT / "docs/服务器使用指南.md", stage / "Server-Guide.md")
    shutil.copyfile(ROOT / "docs/分机使用指南.md", stage / "Client-Guide.md")
    shutil.copyfile(ROOT / "docs/installer-056-change-audit.md", stage / "Installation-Change-Audit.md")
    notes = """0.5.6 修复服务器安装方式，并把指定六文件组网 Skill 更新整合到完整安装包。

### 安装与知识

先正常停止旧服务器，用原 Windows 账号运行完整 EXE，无需卸载。
新版直接安装至独立版本目录，校验和知识更新成功后更新桌面/开始菜单入口。
不再把旧 app 改名到 backup，也不再把 staging 改名为 app；旧程序目录保持原状。
安装后请从更新后的“GWAP 服务器”快捷方式启动，业务数据目录与管理员凭据保持原有设置。

已有服务器：安装中先备份，再自动补齐/更新总领 SKILL.md 和五份 references，并完整构建索引。
不再因为旧库已有知识而跳过，不需要再进入 AI 助手或点击一次导入。
首次空实例：第一次正常启动服务器时自动创建数据库并导入六份文件。
出现 READY 后请保持服务器运行，等待后台索引完成；六份文件整体发布后才会显示。
仅替换来源路径和组网 Skill 类别匹配的包内文档，保留历史版本；其他知识、草稿、案例、报告及独立自定义模板保留。
按当前 Embedding 配置索引（外部 API 配置会向该服务发送待索引知识），不调用诊断 Chat 或 CLI。
数据库备份位于业务目录 knowledge-update-backups。新六文件和索引全部完成后一次发布，失败不发布半个包。

### 最近两版为什么失败

根目录整体替换是实现选择，不是用户要求。0.5.3 为解决逐文件移动可能破坏旧程序的问题改用 Directory.Move；
0.5.4 沿用它，0.5.5 只增加有限重试。公司日志证实两次目录重命名阶段出现 Windows 错误 5/32，
并不能据此认定某个进程或公司安全软件负责。0.5.6 移除了这些重命名调用。
已有库一律 PRESERVED、再人工确认导入也是之前自行加入的保护规则，不符合安装自动内置六文件的需求，本版安装流程已纠正。
另外，旧 AfterInstall 异常处理可能报错后返回成功；本版改用 PrepareToInstall，准备失败明确返回非零并停止安装。
完整时间线及代码依据见 Installation-Change-Audit.md。

### 验证与附件

确切发布 EXE 已执行三个 Win11 场景：服务器锁未释放时退出 7；同时持有旧/新目录句柄时覆盖安装成功并在完成前发布六文件；
全新安装及实际首次服务器启动成功，数据库内六份完整正文和初始管理员存在。
使用实际内置 GGUF 索引，保留旧案例/Wiki、归档原组网根文件并确认备份，旧程序文件未被替换。
另有三项调整后的错误日志检查及工程检查 24/24。没有重复历史成功功能回归，没有诊断模型/API/CLI调用或手动 CI。
这是开发机实际安装验收，不冒充公司服务器已经验收，也不声明 CI 全绿或主分支已合并。

完整 EXE 包含 Python、Embedding/Reranker、网关及原始六文件包。分机继续沿用字节不变的 GWAP-Client-0.5.0.zip。
"""
    (stage / "Release-Notes.md").write_text(notes, encoding="utf-8")
    def asset(path):
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"name": path.name, "path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha}
    assets = [asset(Path(build["installer"]))] + [asset(stage / name) for name in
        (client.name, "Server-Guide.md", "Client-Guide.md", "Installation-Change-Audit.md", "Release-Notes.md")]
    assert assets[0]["sha256"] == setup["installer_sha256"]
    assert assets[1]["sha256"] == "8ed2a944a331780a059142758b948d8579f9bbe5520454d1f6d1f287ba9ae4eb"
    delivery = {"schema_version": 1, "release": "v0.5.6", "source_commit": package["source_commit"],
        "package": package, "setup": setup, "harness": "24/24", "client_unchanged": True,
        "assets": [{k: v for k, v in item.items() if k != "path"} for item in assets]}
    (stage / "delivery-manifest.json").write_text(json.dumps(delivery, ensure_ascii=False, indent=2), encoding="utf-8")
    assets.append(asset(stage / "delivery-manifest.json"))
    (stage / "SHA256.txt").write_text("".join(f"{a['sha256']}  {a['name']}\n" for a in assets), encoding="ascii")
    assets.append(asset(stage / "SHA256.txt"))
    plan = {"tag": "v0.5.6", "repo": "mrrabbitss/debug-platform", "source_commit": package["source_commit"], "assets": assets}
    (stage / "asset-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"assets": len(assets), "source_commit": plan["source_commit"], "installer_sha256": build["sha256"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    prepare(parser.parse_args().directory.resolve(strict=True))
