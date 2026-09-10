"""Entry point shipped in the small offline updater; stdlib-only bootstrap."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def verify_files():
    manifest = json.loads((ROOT / "updater-manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (ROOT / relative).resolve(strict=True)
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("更新文件路径不合法。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("更新包校验失败，请重新完整解压。")
    return manifest


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, value):
        self.console.write(value)
        self.log.write(value)
        self.log.flush()

    def flush(self):
        self.console.flush()
        self.log.flush()


def update(args, manifest):
    package = args.package.resolve(strict=True)
    sys.path.insert(0, str(package))
    sys.path.insert(0, str(ROOT))
    info = json.loads((package / "build-info.json").read_text(encoding="utf-8-sig"))
    if info.get("package_version") not in manifest["supported_versions"]:
        raise ValueError("此更新工具支持服务器 0.5.1 至 0.5.5，当前程序版本不匹配。")
    import server_admin
    import portable_launcher as launcher
    from portable_server_config import load_server_config

    config_path = args.data_root.resolve(strict=True) / "config/server.json"
    config = load_server_config(config_path)
    server_admin._validate_recovery_target(config, config_path)
    config.apply_environment()
    server_admin._prepare_existing_server_environment(config)
    # No installed module/file is overwritten, and no application is started.
    import app.services
    app.services.__path__ = [str(ROOT / "services"), *app.services.__path__]

    def manager():
        specs = tuple(s for s in launcher.load_bundled_model_specs() if s.task_type == "embedding")
        if len(specs) != 1:
            raise ValueError("现有程序缺少完整的内置 Embedding 组件。")
        return launcher.BundledModelManager(specs, data_root=config.root / "data", timeout_seconds=600)

    print("当前程序版本：" + info["package_version"], flush=True)
    print("业务数据目录：" + str(config.root), flush=True)
    # Do not import/create the engine until holding the same lock as the server.
    with server_admin._stopped_server_lock(config):
        from offline_skill_update import run_update
        return run_update(data_root=config.root / "data", source_zip=ROOT / "hilink-diag.zip",
            archive_root=config.root / "knowledge-update-backups", manager_factory=manager,
            allow_external=args.allow_configured_embedding_api)


def main():
    parser = argparse.ArgumentParser(description="只更新组网 Skill，无需重装服务器。")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--allow-configured-embedding-api", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    log_path = ROOT / ("knowledge-update-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".log")
    original = sys.stdout
    with log_path.open("x", encoding="utf-8") as log:
        sys.stdout = Tee(original, log)
        try:
            result = update(args, verify_files())
            print("结果：" + result["status"], flush=True)
            return 0
        except (OSError, ValueError, RuntimeError) as error:
            # Provider errors are normalized by the publication service. Do not
            # print tracebacks containing model config, tokens or document text.
            print("[未完成] " + str(error), flush=True)
            print("本工具不替换程序。未成功发布时旧知识仍生效；请保留本日志。", flush=True)
            return 1
        finally:
            sys.stdout = original
            print("操作日志：" + str(log_path), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
