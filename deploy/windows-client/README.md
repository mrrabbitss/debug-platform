# GWAP 分机客户端

完整解压后，按包内《分机使用指南.md》操作。

- `Install.bat`：安装桌面快捷方式。
- `Open Platform.bat`：用保存的个人身份打开网页。
- `Start.bat`：连接服务器并打开 CodeAgent。
- 内网专用包预置地址，安装时自动配置证书，只需输入个人识别码。
- `Trust Server Certificate.bat`：仅供未预置地址的旧版通用连接器手动导入证书。

需要已有可用的 CodeAgent。本包不安装后端、数据库、Python 或模型。
服务器地址、证书和个人访问令牌由管理员提供。

服务器包含 2026-09-09 专家迭代时，管理员晋升的 EXPERT 继续使用原识别码及分机网页登录
票据（browser-ticket），以服务器返回的当前角色登录。自助注册只新建 ENGINEER；ADMIN 和
停用账号仍拒绝使用这些入口。旧分机如因只接受 ENGINEER 而报 `unexpected identity`，需由
管理员同步更新客户端；不要改用新识别码。管理员账号继续使用管理员令牌入口。
当前源码已同时支持 ENGINEER / EXPERT 的首次登录和已保存令牌复用；已发布的旧分机 ZIP 没有被本次源码改动替换。
