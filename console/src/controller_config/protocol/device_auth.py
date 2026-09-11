from __future__ import annotations

import base64
import json
import re
import secrets
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from controller_config.protocol.framing import canonical_json_bytes


AUTH_GET_CERTIFICATE = 0x04
AUTH_CHALLENGE = 0x05
AUTH_SIGNATURE_ALGORITHM = "rsa-pss-sha256-salt32"
AUTH_PUBLIC_KEY_ALGORITHM = "rsa-3072"
AUTH_DOMAIN = "BORING-WMP1-DEVICE-AUTH-V1"
AUTH_NONCE_BYTES = 32
AUTH_NONCE_ENCODED_LENGTH = 43

_CERTIFICATE_FIELDS = {
    "version",
    "issuer_key_id",
    "product_id",
    "hardware_id",
    "serial",
    "public_key_algorithm",
    "public_key_spki",
}
_BASE64URL_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class TrustPolicy(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class DeviceTrustState(str, Enum):
    AUTHENTICATED = "authenticated"
    DEVELOPMENT_UNAUTHENTICATED = "development_unauthenticated"


@dataclass(frozen=True)
class TrustedRoot:
    issuer_key_id: str
    public_key_algorithm: str
    public_key_spki: str
    purpose: str
    public_key: rsa.RSAPublicKey


@dataclass(frozen=True)
class TrustBundle:
    policy: TrustPolicy
    roots: dict[str, TrustedRoot]

    @classmethod
    def from_document(
        cls,
        document: dict[str, Any],
        *,
        policy: TrustPolicy,
    ) -> "TrustBundle":
        if document.get("version") != 1:
            raise ValueError("设备信任根文件版本必须为 1")
        raw_roots = document.get("roots")
        if not isinstance(raw_roots, list):
            raise ValueError("设备信任根 roots 必须是数组")
        roots: dict[str, TrustedRoot] = {}
        for index, raw_root in enumerate(raw_roots):
            if not isinstance(raw_root, dict):
                raise ValueError(f"设备信任根 roots[{index}] 必须是 object")
            secret_fields = sorted(
                key
                for key in raw_root
                if "private" in key.lower() or "hmac" in key.lower()
            )
            if secret_fields:
                raise ValueError(
                    "设备信任根文件禁止包含私钥或 HMAC 材料："
                    + ", ".join(secret_fields)
                )
            issuer_key_id = raw_root.get("issuer_key_id")
            algorithm = raw_root.get("public_key_algorithm")
            spki = raw_root.get("public_key_spki")
            purpose = raw_root.get("purpose")
            if not all(isinstance(value, str) and value for value in (issuer_key_id, algorithm, spki)):
                raise ValueError(f"设备信任根 roots[{index}] 缺少公开密钥字段")
            if purpose not in {"test", "production"}:
                raise ValueError(f"设备信任根 roots[{index}].purpose 必须是 test 或 production")
            if algorithm != AUTH_PUBLIC_KEY_ALGORITHM:
                raise ValueError(
                    f"设备信任根 roots[{index}] 必须使用 {AUTH_PUBLIC_KEY_ALGORITHM}"
                )
            try:
                public_key = _load_rsa_public_key(spki)
            except ValueError as exc:
                raise ValueError(f"设备信任根 roots[{index}] 公钥无效：{exc}") from exc
            if issuer_key_id in roots:
                raise ValueError(f"设备信任根 issuer_key_id 重复：{issuer_key_id}")
            roots[issuer_key_id] = TrustedRoot(
                issuer_key_id=issuer_key_id,
                public_key_algorithm=algorithm,
                public_key_spki=spki,
                purpose=purpose,
                public_key=public_key,
            )
        return cls(policy=policy, roots=roots)

    @classmethod
    def load(
        cls,
        root_path: Path,
        *,
        policy: TrustPolicy,
    ) -> "TrustBundle":
        try:
            document = json.loads(root_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取设备信任根：{exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("设备信任根文件必须是 JSON object")
        return cls.from_document(document, policy=policy)

    def root_for(self, issuer_key_id: str) -> TrustedRoot | None:
        root = self.roots.get(issuer_key_id)
        if root is None:
            return None
        if self.policy is TrustPolicy.PRODUCTION and root.purpose != "production":
            return None
        return root


@dataclass(frozen=True)
class VerifiedDeviceCertificate:
    certificate: dict[str, Any]
    public_key: rsa.RSAPublicKey

    @property
    def serial(self) -> str:
        return str(self.certificate["serial"])

    @property
    def issuer_key_id(self) -> str:
        return str(self.certificate["issuer_key_id"])


@dataclass(frozen=True)
class DeviceTrust:
    state: DeviceTrustState
    message: str
    technical: str
    issuer_key_id: str = ""
    serial: str = ""

    @property
    def is_authenticated(self) -> bool:
        return self.state is DeviceTrustState.AUTHENTICATED


class DeviceAuthenticationError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class DeviceAuthenticator:
    def __init__(
        self,
        trust_bundle: TrustBundle,
        *,
        nonce_factory: Callable[[], bytes] | None = None,
    ) -> None:
        self.trust_bundle = trust_bundle
        self._nonce_factory = nonce_factory or (lambda: secrets.token_bytes(AUTH_NONCE_BYTES))

    def new_nonce(self) -> str:
        value = self._nonce_factory()
        if not isinstance(value, bytes) or len(value) != AUTH_NONCE_BYTES:
            raise ValueError("认证 nonce 生成器必须返回 32 字节")
        return _base64url_encode(value)

    def trust_without_device_identity(self, technical: str) -> DeviceTrust:
        if self.trust_bundle.policy is TrustPolicy.PRODUCTION:
            raise DeviceAuthenticationError(
                "authentication_required",
                "正式版 BORING 控制台要求设备认证能力",
            )
        return DeviceTrust(
            DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED,
            "开发设备，未认证",
            technical,
        )

    def verify_certificate_response(
        self,
        payload: dict[str, Any],
        *,
        hello_identity: dict[str, Any],
        usb_serial: str,
    ) -> VerifiedDeviceCertificate:
        result = _command_result(payload, "AUTH_GET_CERTIFICATE")
        if set(result) != {"certificate", "issuer_signature", "signature_algorithm"}:
            raise DeviceAuthenticationError(
                "malformed_certificate",
                "设备证书响应字段不符合认证合同",
            )
        if result.get("signature_algorithm") != AUTH_SIGNATURE_ALGORITHM:
            raise DeviceAuthenticationError(
                "unsupported_algorithm",
                "设备证书使用了控制台不支持的签名算法",
            )
        certificate = result.get("certificate")
        if not isinstance(certificate, dict) or set(certificate) != _CERTIFICATE_FIELDS:
            raise DeviceAuthenticationError(
                "malformed_certificate",
                "设备证书字段不符合认证合同",
            )
        self._validate_certificate_fields(certificate)
        issuer_key_id = str(certificate["issuer_key_id"])
        root = self.trust_bundle.root_for(issuer_key_id)
        if root is None:
            raise DeviceAuthenticationError(
                "unknown_issuer",
                f"设备证书签发方不在当前信任范围：{issuer_key_id}",
            )
        if root.public_key_algorithm != AUTH_PUBLIC_KEY_ALGORITHM:
            raise DeviceAuthenticationError(
                "unsupported_algorithm",
                "设备根公钥算法与认证合同不一致",
            )
        try:
            issuer_signature = _base64url_decode(
                result.get("issuer_signature"),
                "issuer_signature",
            )
            _verify_pss(
                root.public_key,
                canonical_json_bytes(certificate),
                issuer_signature,
            )
        except (ValueError, InvalidSignature) as exc:
            raise DeviceAuthenticationError(
                "certificate_signature",
                "设备证书签名验证失败",
            ) from exc
        self._validate_identity(certificate, hello_identity, usb_serial)
        try:
            device_key = _load_rsa_public_key(str(certificate["public_key_spki"]))
        except ValueError as exc:
            raise DeviceAuthenticationError(
                "malformed_certificate",
                "设备证书公钥格式无效",
            ) from exc
        return VerifiedDeviceCertificate(dict(certificate), device_key)

    def verify_challenge_response(
        self,
        payload: dict[str, Any],
        *,
        certificate: VerifiedDeviceCertificate,
        nonce: str,
    ) -> DeviceTrust:
        _validate_nonce(nonce)
        result = _command_result(payload, "AUTH_CHALLENGE")
        if set(result) != {"nonce", "signature_algorithm", "signature"}:
            raise DeviceAuthenticationError(
                "malformed_challenge",
                "设备挑战响应字段不符合认证合同",
            )
        if result.get("signature_algorithm") != AUTH_SIGNATURE_ALGORITHM:
            raise DeviceAuthenticationError(
                "unsupported_algorithm",
                "设备挑战响应使用了控制台不支持的签名算法",
            )
        if result.get("nonce") != nonce:
            raise DeviceAuthenticationError(
                "nonce_mismatch",
                "设备返回的 nonce 与当前连接挑战不一致",
            )
        signing_object = {
            "domain": AUTH_DOMAIN,
            "nonce": nonce,
            "serial": certificate.serial,
        }
        try:
            signature = _base64url_decode(result.get("signature"), "signature")
            _verify_pss(
                certificate.public_key,
                canonical_json_bytes(signing_object),
                signature,
            )
        except (ValueError, InvalidSignature) as exc:
            raise DeviceAuthenticationError(
                "challenge_signature",
                "设备挑战签名验证失败",
            ) from exc
        return DeviceTrust(
            DeviceTrustState.AUTHENTICATED,
            "BORING 设备已认证",
            f"issuer={certificate.issuer_key_id}",
            issuer_key_id=certificate.issuer_key_id,
            serial=certificate.serial,
        )

    @staticmethod
    def _validate_certificate_fields(certificate: dict[str, Any]) -> None:
        if certificate.get("version") != 1:
            raise DeviceAuthenticationError("malformed_certificate", "设备证书 version 必须为 1")
        if certificate.get("public_key_algorithm") != AUTH_PUBLIC_KEY_ALGORITHM:
            raise DeviceAuthenticationError(
                "unsupported_algorithm",
                "设备证书公钥算法与认证合同不一致",
            )
        for field in ("issuer_key_id", "product_id", "hardware_id", "serial", "public_key_spki"):
            value = certificate.get(field)
            if not isinstance(value, str) or not value:
                raise DeviceAuthenticationError(
                    "malformed_certificate",
                    f"设备证书 {field} 必须是非空字符串",
                )

    @staticmethod
    def _validate_identity(
        certificate: dict[str, Any],
        hello_identity: dict[str, Any],
        usb_serial: str,
    ) -> None:
        for field in ("product_id", "hardware_id", "serial"):
            if certificate[field] != hello_identity.get(field):
                raise DeviceAuthenticationError(
                    "identity_mismatch",
                    f"设备证书 {field} 与 HELLO 身份不一致",
                )
        if usb_serial and certificate["serial"] != usb_serial:
            raise DeviceAuthenticationError(
                "identity_mismatch",
                "设备证书 serial 与 USB serial 不一致",
            )


def load_authenticator(
    roots_path: Path,
    *,
    policy_marker_path: Path | None = None,
) -> DeviceAuthenticator:
    policy = TrustPolicy.DEVELOPMENT
    if policy_marker_path is not None and policy_marker_path.is_file():
        try:
            marker = json.loads(policy_marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取设备信任策略：{exc}") from exc
        if marker != {"version": 1, "policy": TrustPolicy.PRODUCTION.value}:
            raise ValueError("设备信任策略标记必须固定为 production v1")
        policy = TrustPolicy.PRODUCTION
    bundle = TrustBundle.load(roots_path, policy=policy)
    if policy is TrustPolicy.PRODUCTION and not any(
        root.purpose == "production" for root in bundle.roots.values()
    ):
        raise ValueError("正式安装包至少需要一个有效的生产信任根")
    return DeviceAuthenticator(bundle)


def load_default_authenticator() -> DeviceAuthenticator:
    asset_root = files("controller_config.assets")
    roots_path = Path(str(asset_root.joinpath("device-trust-roots.json")))
    marker_resource = asset_root.joinpath("device-trust-policy.json")
    marker_path = Path(str(marker_resource)) if marker_resource.is_file() else None
    return load_authenticator(roots_path, policy_marker_path=marker_path)


def _command_result(payload: dict[str, Any], command: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("command") != command:
        raise DeviceAuthenticationError(
            "malformed_response",
            f"设备认证响应 command 必须为 {command}",
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise DeviceAuthenticationError(
            "malformed_response",
            f"{command} result 必须是 object",
        )
    return result


def _load_rsa_public_key(value: str) -> rsa.RSAPublicKey:
    decoded = _base64url_decode(value, "public_key_spki")
    try:
        key = serialization.load_der_public_key(decoded)
    except ValueError as exc:
        raise ValueError("public_key_spki 不是有效 DER SPKI") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("public_key_spki 不是 RSA 公钥")
    numbers = key.public_numbers()
    if key.key_size != 3072 or numbers.e != 65537:
        raise ValueError("RSA 公钥必须为 3072 bit 且公开指数为 65537")
    return key


def _verify_pss(key: rsa.RSAPublicKey, message: bytes, signature: bytes) -> None:
    key.verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )


def _validate_nonce(value: str) -> None:
    if not isinstance(value, str) or len(value) != AUTH_NONCE_ENCODED_LENGTH:
        raise DeviceAuthenticationError(
            "malformed_nonce",
            "认证 nonce 必须是 43 字符无填充 base64url",
        )
    try:
        decoded = _base64url_decode(value, "nonce")
    except ValueError as exc:
        raise DeviceAuthenticationError("malformed_nonce", str(exc)) from exc
    if len(decoded) != AUTH_NONCE_BYTES:
        raise DeviceAuthenticationError("malformed_nonce", "认证 nonce 解码后必须是 32 字节")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: object, label: str) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or "=" in value
        or _BASE64URL_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"{label} 必须是无填充 base64url")
    padding_length = (-len(value)) % 4
    try:
        return base64.b64decode(
            value + ("=" * padding_length),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"{label} 不是有效 base64url") from exc
