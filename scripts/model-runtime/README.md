# Win11 全 GGUF 检索运行时资产

本目录定义首发离线检索包的可复现输入，不包含模型权重、密钥、Python
虚拟环境或构建产物。安装器使用同一个 `llama-server.exe` 分别启动
Embedding 和 Reranker sidecar；诊断 Chat 模型仍由用户配置 API，不进入离线包。

## 固定组合

| 组件 | 固定来源与版本 | 发布格式 | 许可证 |
| --- | --- | --- | --- |
| Runtime | `ggml-org/llama.cpp` `b10729` / `458681e1d5d4a29a1463c4732e03226cf384b997` | 官方 Win CPU x64 ZIP | MIT；同时分发 ZIP 内 `LICENSE-LLVM-OpenMP` |
| Native CRT | `Microsoft.VisualCpp.CRT.Redist.X64` `14.44.35211.0` | 微软不可变 VSIX 中的未修改 x64 app-local release DLL | `LicenseRef-Microsoft-Visual-Studio-2022-Redistributable`；发布者必须满足微软许可，不是开源 SPDX 许可证 |
| Embedding | `BAAI/bge-base-zh-v1.5` / `f03589ceff5aac7111bd60cfc7d497ca17ecac65` | 从固定原始快照转换为 F16 GGUF | MIT |
| Reranker | 上游 `Qwen/Qwen3-Reranker-0.6B` / `e61197ed45024b0ed8a2d74b80b4d909f1255473` | llama.cpp 官方组织 `ggml-org` 固定 revision `a02f48bb4f057028298c21fa033da2b30d7742d5` 的 Q8_0 GGUF | Apache-2.0 |

`model-assets.json` 记录下载大小与 SHA-256；`model-assets.schema.json`
约束其结构。受控转换得到的 BGE F16 已锁定为 `204756128` 字节、SHA-256
`677d0074629b28c96860d258d04c5197996d3f422cba84badadd64090e085f35`；准备脚本
必须生成完全相同的字节才会原子发布输出和生成组件锁。Qwen 预构建文件来自
llama.cpp 的官方 GitHub 组织 `ggml-org`，已通过 Hugging Face revision API 确认大小
`639153184` 和 LFS SHA-256
`22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48`，
准备脚本在下载后仍会重新计算完整文件 SHA-256。这里的“官方”指 llama.cpp
发布组织，不表示该转换件由 Qwen 团队发布；模型上游和许可证仍追溯到 Qwen。
若该预构建仓不可达，
`--qwen-from-source` 会改用官方 Qwen 固定 revision 的 safetensors，先转换
F16 GGUF，再用同一固定 llama.cpp 的 `llama-quantize.exe` 生成 Q8_0。该回退
输出的实际 SHA-256 会进入 `components.lock.json`，但在通过质量 Golden
Dataset 前仍不能把清单状态改成 `release_candidate`。

CRT 使用微软官方不可变 `base.vsix`，以实际 HTTP 下载的 `3224191` 字节和
SHA-256 `4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5`
为准，不信任上游目录中可能漂移的自报大小。准备脚本仅允许清单列出的 10 个
`Microsoft.VC143.CRT` release DLL，明确拒绝 `debug_nonredist`，逐文件复核哈希；
构建时还要求每个源 DLL 的 Microsoft Authenticode 有效。它们以 app-local 方式放在
`runtime/llama`，目标机无需管理员权限或预装 VC++ Redistributable。

## 使用

所有命令从仓库根目录运行，转换阶段要求 Python 3.12 和 Git。默认调用只显示
计划，不访问网络，也不写文件：

```bat
py -3.12 scripts\model-runtime\prepare_assets.py
```

准备默认的固定预构建 Qwen 路径：

```bat
py -3.12 scripts\model-runtime\prepare_assets.py --all ^
  --cache-root artifacts\build-cache\windows-x64
```

公司网络可显式切到 HTTPS 镜像；所有最终字节仍按原始清单 SHA-256 校验：

```bat
py -3.12 scripts\model-runtime\prepare_assets.py --all ^
  --hf-endpoint https://hf-mirror.com ^
  --cache-root artifacts\build-cache\windows-x64
```

从官方 Qwen 原始权重构建回退版本，并跳过社区预构建下载：

```bat
py -3.12 scripts\model-runtime\prepare_assets.py --all --qwen-from-source ^
  --hf-endpoint https://hf-mirror.com ^
  --cache-root artifacts\build-cache\windows-x64
```

`--all` 依次完成固定输入下载、runtime 安全解压、隔离转换环境创建、BGE F16
转换和 `components.lock.json` 生成。也可单独运行：

- `--download`：断点续传并验证默认输入；
- `--extract-runtime`：只提取 `llama-server.exe`、全部 CPU DLL 和第三方许可；
- `--convert-bge`：在缓存的 `build/convert-venv` 中转换 BGE；
- `--qwen-from-source`：下载官方 Qwen 源文件并执行回退转换；
- `--write-component-lock`：扫描实际文件并生成安装器严格锁；
- `--offline`：禁止网络与依赖安装，只复用已经准备好的缓存。

完整缓存准备成功后，后续安装器装配不需要网络，也不需要再次转换。保留整个
`artifacts/build-cache/windows-x64`（尤其是 `llama/`、`models/`、`licenses/`
和 `components.lock.json`），可直接执行：

```bat
py -3.12 scripts\model-runtime\validate_assets.py ^
  --asset-root artifacts\build-cache\windows-x64 ^
  --component-lock artifacts\build-cache\windows-x64\components.lock.json

scripts\build_windows_gguf_installer.bat -CacheRoot artifacts\build-cache\windows-x64
```

`validate_assets.py --strict-release` 还要求 BGE/Qwen 的质量状态和 bundle 状态完成
晋级，并完成上游一致性、检索 Golden Dataset、冷启动、升级与卸载质量门禁。

## 已完成的真实语义冒烟

使用当前锁定缓存、真实 `llama-server` 和两个 GGUF 运行
`smoke_runtime.py` 已通过：

- BGE 返回有限值、L2 归一化的 768 维向量；合成 AP 离线诊断文本相似度
  `0.5609`，高于无关包装文本的 `0.1655`；
- Qwen Reranker 将相关诊断文本排在第一位，得分 `0.9997`，无关文本
  `0.0001`；
- 两个 sidecar 均在动态 `127.0.0.1` 端口启动，并使用临时 Bearer key；
- Windows 冒烟枚举两个 sidecar 的实际已加载模块，确认 `MSVCP140.dll`、
  `VCRUNTIME140.dll`、`VCRUNTIME140_1.dll` 来自包内 `runtime/llama`，不会被开发机
  `System32` 中的全局 CRT 掩盖；
- pinned b10729 的 `--reranking` 参数已实际启用 `/v1/rerank`。该版本帮助文本也将
  `--rerank/--reranking` 定义为启用 reranking endpoint，因此无需额外添加
  `--embedding`。

这是小规模 API/语义健全性检查，不等同于与上游模型对齐的完整质量评测。

## 运行参数与边界

- BGE：`--embedding --pooling cls --embd-normalize 2 --ctx-size 512`，查询侧需由
  平台添加中文检索前缀，文档侧不添加。
- Qwen：`--reranking --pooling rank --ctx-size 8192 --parallel 1`，首发仅单并发，
  避免 8 GB 电脑发生明显内存压力。
- 两个 sidecar 只绑定 `127.0.0.1`，端口与随机 API key 由 portable launcher
  添加；不得把服务暴露到局域网。
- Win11 x64、4 核 CPU、8 GB RAM 是最低边界，建议 6 核及 16 GB RAM；8 GB
  设备应按需启动 Reranker。首发 CPU 包不包含 CUDA、Vulkan 或厂商 GPU DLL。
- 默认固定下载约 `0.994 GiB`；锁定检索组件为 `889538161` 字节，约 `0.83 GiB`，加现有 Portable
  Core 后预计安装体积 `1.1-1.3 GiB`。默认构建建议预留 `5 GiB`，官方 Qwen
  源码回退建议预留 `8 GiB`。

## 尚未验证的发布门禁

当前清单状态有意保持 `experimental_unverified`，原因如下：

1. llama.cpp 转换依赖来自固定 commit 的 requirements，但 Python wheel 集还需
   在 Release CI 中固化为离线 wheelhouse/哈希锁，才能保证跨时间完全复现；
2. 当前真实语义冒烟已经通过，但 BGE 与上游向量基线的容差、Qwen 排序/NDCG
   和完整检索 Golden Dataset 尚未达到 `quality_verified`；
3. Qwen 官方源转换回退尚需与 `ggml-org` 固定预构建输出做功能等价验证；
4. 全新 Win11 CPU 电脑的冷启动、低内存、升级回滚和卸载尚需 Release CI/实机
   证明。

在这些门禁完成前，脚本可以生成逐文件哈希锁并供内部试装，但不得把它描述为
已验证的正式离线发行版。
