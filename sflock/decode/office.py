# Copyright (C) 2017 Jurriaan Bremer.
# This file is part of SFlock - http://www.sflock.org/.
# See the file 'docs/LICENSE.txt' for copying permission.

import hashlib
import struct
import xml.dom.minidom

try:
    from Crypto.Cipher import AES, PKCS1_v1_5
    from Crypto.PublicKey import RSA
    HAVE_PYCRYPTO = True
except ImportError:
    HAVE_PYCRYPTO = False

from sflock.abstracts import Decoder, File
from sflock.exception import DecoderException

class EncryptedInfo(object):
    key_data_salt = None
    key_data_hash_alg = None
    verifier_hash_input = None
    verifier_hash_value = None
    encrypted_key_value = None
    spin_value = None
    password_salt = None
    password_hash_alg = None
    password_key_bits = None

class Office(Decoder):
    name = "office"

    def init(self):
        if not HAVE_PYCRYPTO:
            raise DecoderException(
                "Microsoft Office document decoding is only supported on "
                "Linux systems or when manually installing PyCrypto!"
            )

        self.secret_key = None
        self.verifier_hash_input = None
        self.verifier_hash_value = None

    def get_hash(self, value, algorithm):
        if algorithm == "SHA512":
            return hashlib.sha512(value).digest()
        else:
            return hashlib.sha1(value).digest()

    def gen_encryption_key(self, block):
        # Initial round sha512(salt + password).
        h = self.get_hash(
            self.ei.password_salt + self.password.encode("utf-16le"),
            self.ei.password_hash_alg
        )

        # Iteration of 0 -> spincount-1; hash = sha512(iterator + hash).
        for i in xrange(self.ei.spin_value):
            h = self.get_hash(
                struct.pack("<I", i) + h, self.ei.password_hash_alg
            )

        # Final skey and truncation.
        h = self.get_hash(h + block, self.ei.password_hash_alg)
        skey = h[:self.ei.password_key_bits/8]
        return skey

    def init_secret_key(self):
        # TODO Add support for private keys.
        if False:
            rsa = PKCS1_v1_5.new(RSA.importKey(self._private_key))
            self.secret_key = rsa.decrypt(self.ei.encrypted_key_value, None)
            # Presumably the following is correct.
            self.verifier_hash_input = rsa.decrypt(
                self.ei.verifier_hash_input, None
            )
            self.verifier_hash_value = rsa.decrypt(
                self.ei.verifier_hash_value, None
            )

        if self.password:
            block_verifier_input = bytearray([
                0xfe, 0xa7, 0xd2, 0x76, 0x3b, 0x4b, 0x9e, 0x79
            ])
            block_verifier_value = bytearray([
                0xd7, 0xaa, 0x0f, 0x6d, 0x30, 0x61, 0x34, 0x4e
            ])
            block_encrypted_key = bytearray([
                0x14, 0x6e, 0x0b, 0xe7, 0xab, 0xac, 0xd0, 0xd6,
            ])

            # AES decrypt the encrypted* values with their pre-defined block
            # keys and salt in order to get secret key.
            aes = AES.new(
                self.gen_encryption_key(block_verifier_input),
                AES.MODE_CBC, self.ei.password_salt
            )
            self.verifier_hash_input = aes.decrypt(
                self.ei.verifier_hash_input
            )

            aes = AES.new(
                self.gen_encryption_key(block_verifier_value),
                AES.MODE_CBC, self.ei.password_salt
            )
            self.verifier_hash_value = aes.decrypt(
                self.ei.verifier_hash_value
            )

            aes = AES.new(
                self.gen_encryption_key(block_encrypted_key),
                AES.MODE_CBC, self.ei.password_salt
            )
            self.secret_key = aes.decrypt(self.ei.encrypted_key_value)

    def decrypt_blob(self, f):
        ret = []
        # TODO Ensure that the assumption of "total size" being a 64-bit
        # integer is correct?
        for idx in xrange(0, struct.unpack("Q", f.read(8))[0], 0x1000):
            iv = self.get_hash(
                self.ei.key_data_salt + struct.pack("<I", idx),
                self.ei.key_data_hash_alg
            )
            aes = AES.new(self.secret_key, AES.MODE_CBC, iv[:16])
            ret.append(aes.decrypt(f.read(0x1000)))
        return File(contents="".join(ret))

    def decode(self):
        if not self.f.ole:
            return

        if ["EncryptionInfo"] not in self.f.ole.listdir():
            return

        info = xml.dom.minidom.parseString(
            self.f.ole.openstream("EncryptionInfo").read()[8:]
        )
        key_data = info.getElementsByTagName("keyData")[0]
        password = info.getElementsByTagName("p:encryptedKey")[0]

        self.ei = ei = EncryptedInfo()
        ei.key_data_salt = key_data.getAttribute("saltValue").decode("base64")
        ei.key_data_hash_alg = key_data.getAttribute("hashAlgorithm")
        ei.verifier_hash_input = password.getAttribute(
            "encryptedVerifierHashInput"
        ).decode("base64")
        ei.verifier_hash_value = password.getAttribute(
            "encryptedVerifierHashValue"
        ).decode("base64")
        ei.encrypted_key_value = password.getAttribute(
            "encryptedKeyValue"
        ).decode("base64")
        ei.spin_value = int(password.getAttribute("spinCount"))
        ei.password_salt = password.getAttribute("saltValue").decode("base64")
        ei.password_hash_alg = password.getAttribute("hashAlgorithm")
        ei.password_key_bits = int(password.getAttribute("keyBits"))

        self.init_secret_key()

        verifier_hash = self.get_hash(
            self.verifier_hash_input, self.ei.password_hash_alg
        )
        # Incorrect password.
        if verifier_hash != self.verifier_hash_value:
            return False

        return self.decrypt_blob(self.f.ole.openstream("EncryptedPackage"))
