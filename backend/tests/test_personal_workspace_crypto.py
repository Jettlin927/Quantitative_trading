from __future__ import annotations

import unittest

from backend.app.personal_workspace.crypto import (
    EncryptedEnvelope,
    FixedKeyring,
    PersonalDataCipher,
)


class PersonalWorkspaceCryptoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.keyring = FixedKeyring(
            active_key_id="synthetic-key-v2",
            data_keys={
                "synthetic-key-v1": bytes(range(32)),
                "synthetic-key-v2": bytes(reversed(range(32))),
            },
            lookup_key=b"synthetic-lookup-key-for-tests-only",
        )
        self.cipher = PersonalDataCipher(self.keyring)
        self.aad = "private_workbench|personal_holdings|holding-1|payload|1"

    def test_aes_gcm_round_trip_binds_payload_to_aad(self) -> None:
        envelope = self.cipher.encrypt_json(
            {"symbol": "SYNTH-001", "quantity": "12.50"},
            aad=self.aad,
        )

        self.assertEqual(envelope.key_id, "synthetic-key-v2")
        self.assertEqual(envelope.payload_schema, "1")
        self.assertNotIn(b"SYNTH-001", envelope.ciphertext)
        self.assertEqual(
            self.cipher.decrypt_json(envelope, aad=self.aad),
            {"quantity": "12.50", "symbol": "SYNTH-001"},
        )

        with self.assertRaisesRegex(ValueError, "解密失败"):
            self.cipher.decrypt_json(envelope, aad=f"{self.aad}-wrong")

    def test_wrong_key_is_rejected_and_rotation_dry_run_does_not_reencrypt(self) -> None:
        old_cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="synthetic-key-v1",
                data_keys=self.keyring.data_keys,
                lookup_key=self.keyring.lookup_key,
            )
        )
        envelope = old_cipher.encrypt_json({"note": "synthetic only"}, aad=self.aad)
        wrong_key_envelope = EncryptedEnvelope(
            ciphertext=envelope.ciphertext,
            nonce=envelope.nonce,
            key_id="synthetic-key-v2",
            payload_schema=envelope.payload_schema,
        )

        with self.assertRaisesRegex(ValueError, "解密失败"):
            self.cipher.decrypt_json(wrong_key_envelope, aad=self.aad)

        plan = self.cipher.plan_rotation([envelope], aad_values=[self.aad])
        self.assertEqual(plan.total, 1)
        self.assertEqual(plan.requires_rotation, 1)
        self.assertEqual(plan.active_key_id, "synthetic-key-v2")
        self.assertEqual(envelope.key_id, "synthetic-key-v1")

    def test_nonce_is_unique_and_symbol_lookup_is_stable_without_plaintext(self) -> None:
        first = self.cipher.encrypt_json({"symbol": "SYNTH-001"}, aad=self.aad)
        second = self.cipher.encrypt_json({"symbol": "SYNTH-001"}, aad=self.aad)
        first_lookup = self.cipher.symbol_lookup(
            workspace_id="workspace-1",
            normalized_symbol="SYNTH-001",
        )

        self.assertEqual(len(first.nonce), 12)
        self.assertNotEqual(first.nonce, second.nonce)
        self.assertEqual(
            first_lookup,
            self.cipher.symbol_lookup(
                workspace_id="workspace-1",
                normalized_symbol="SYNTH-001",
            ),
        )
        self.assertNotIn("SYNTH-001", first_lookup)


if __name__ == "__main__":
    unittest.main()
