from __future__ import annotations

from dataclasses import dataclass, field
import base64
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class FixedKeyring:
    active_key_id: str
    data_keys: Mapping[str, bytes] = field(repr=False)
    lookup_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.active_key_id not in self.data_keys:
            raise ValueError("active_key_id 必须存在于 data_keys")
        if any(len(key) != 32 for key in self.data_keys.values()):
            raise ValueError("AES-256-GCM data key 必须为 32 bytes")
        if len(self.lookup_key) < 32:
            raise ValueError("lookup key 至少为 32 bytes")


@dataclass(frozen=True)
class EncryptedEnvelope:
    ciphertext: bytes
    nonce: bytes
    key_id: str
    payload_schema: str


@dataclass(frozen=True)
class RotationPlan:
    total: int
    requires_rotation: int
    active_key_id: str


class PersonalDataCipher:
    def __init__(self, keyring: FixedKeyring) -> None:
        self._keyring = keyring

    def encrypt_json(
        self,
        value: Any,
        *,
        aad: str,
        payload_schema: str = "1",
    ) -> EncryptedEnvelope:
        nonce = os.urandom(12)
        plaintext = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        key_id = self._keyring.active_key_id
        ciphertext = AESGCM(self._keyring.data_keys[key_id]).encrypt(
            nonce,
            plaintext,
            aad.encode("utf-8"),
        )
        return EncryptedEnvelope(
            ciphertext=ciphertext,
            nonce=nonce,
            key_id=key_id,
            payload_schema=payload_schema,
        )

    def decrypt_json(self, envelope: EncryptedEnvelope, *, aad: str) -> Any:
        key = self._keyring.data_keys.get(envelope.key_id)
        if key is None:
            raise ValueError("解密失败：未知 key identity")
        try:
            plaintext = AESGCM(key).decrypt(
                envelope.nonce,
                envelope.ciphertext,
                aad.encode("utf-8"),
            )
        except (InvalidTag, ValueError) as exc:
            raise ValueError("解密失败：key 或 AAD 不匹配") from exc
        return json.loads(plaintext.decode("utf-8"))

    def symbol_lookup(self, *, workspace_id: str, normalized_symbol: str) -> str:
        return self.scoped_lookup(
            workspace_id=workspace_id,
            value=normalized_symbol,
        )

    def scoped_lookup(self, *, workspace_id: str, value: str) -> str:
        digest = hmac.new(
            self._keyring.lookup_key,
            f"{workspace_id}|{value}".encode("utf-8"),
            sha256,
        )
        return digest.hexdigest()

    def plan_rotation(
        self,
        envelopes: Sequence[EncryptedEnvelope],
        *,
        aad_values: Sequence[str],
    ) -> RotationPlan:
        if len(envelopes) != len(aad_values):
            raise ValueError("rotation dry-run 的 envelope 与 AAD 数量必须一致")
        requires_rotation = 0
        for envelope, aad in zip(envelopes, aad_values, strict=True):
            self.decrypt_json(envelope, aad=aad)
            if envelope.key_id != self._keyring.active_key_id:
                requires_rotation += 1
        return RotationPlan(
            total=len(envelopes),
            requires_rotation=requires_rotation,
            active_key_id=self._keyring.active_key_id,
        )


def load_keyring_file(path: str | Path) -> FixedKeyring:
    return _load_keyring_payload(Path(path).read_text(encoding="utf-8"))


def load_owner_only_keyring_file(path: str | Path) -> FixedKeyring:
    from .owner_only_file import read_owner_only_file

    raw = read_owner_only_file(path, maximum_bytes=1024 * 1024)
    return _load_keyring_payload(raw.decode("utf-8", errors="strict"))


def _load_keyring_payload(raw: str) -> FixedKeyring:
    payload = json.loads(raw)
    active_key_id = str(payload["active_key_id"])
    encoded_keys = payload["data_keys"]
    if not isinstance(encoded_keys, dict):
        raise ValueError("data_keys 必须是 key identity 到 base64 key 的映射")
    data_keys = {
        str(key_id): base64.b64decode(str(encoded), validate=True)
        for key_id, encoded in encoded_keys.items()
    }
    lookup_key = base64.b64decode(str(payload["lookup_key"]), validate=True)
    return FixedKeyring(
        active_key_id=active_key_id,
        data_keys=data_keys,
        lookup_key=lookup_key,
    )
