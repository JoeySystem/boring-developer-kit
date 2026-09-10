# WMP1 协议参考

- [protocol.md](protocol.md)：USB CDC / BLE 传输、帧格式、命令和错误码。
- [config-schema.json](config-schema.json)：配置 JSON Schema。
- [generated/protocol_contract.h](generated/protocol_contract.h)：与此快照一致的现有常量头，不包含完整固件。
- [device-trust-roots.json](device-trust-roots.json)：当前官方量产根公钥，仅用于验证，不含签发私钥。
- [fixtures/manifest.json](fixtures/manifest.json)：22 个公开正例、反例和测试向量的索引。

本目录来自 2026-09-10 当前协议快照；线协议为 1.0，配置 Schema 为 1。支持某条命令仍需查询实际设备 CAPABILITIES，不能仅凭协议文件存在就认为所有固件支持。

认证正例 `device-auth-v1.json` 仅保留测试根公钥、公开证书、签名、挑战和已有 canonical 数据；测试私钥已从公开版本移除。这里的测试根不能用于认证官方量产设备，该测试文件不包含官方生产根或签发能力；官方验证公钥单独放在 device-trust-roots.json。负例仍可用于验证失败路径。

本包不包含原项目依赖私有固件源码的内部测试；独立验证见仓库 `tests/`。协议发布不等于扩展获准发送任意设备命令，SDK 的边界仍见 [扩展文档](../docs/extensions.md)。
