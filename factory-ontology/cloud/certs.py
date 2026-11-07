"""IAM Roles Anywhere 用の CA と、抽出プログラムの証明書を手元で作る。秘密鍵はどこにも送らない。"""

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CA_DAYS = 730
CLIENT_DAYS = 90


def name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "XCP"), x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def key_usage(digital_signature, key_cert_sign):
    return x509.KeyUsage(digital_signature=digital_signature, key_cert_sign=key_cert_sign, crl_sign=key_cert_sign,
                         content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                         encipher_only=False, decipher_only=False)


def create_ca(common_name):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    subject = name(common_name)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(key_usage(False, True), critical=True)
            .sign(key, hashes.SHA256()))
    return key_pem(key), cert_pem(cert)


def create_client(common_name, ca_key_pem, ca_cert_pem):
    ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name(common_name)).issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=CLIENT_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(key_usage(True, False), critical=True)
            .sign(ca_key, hashes.SHA256()))
    return key_pem(key), cert_pem(cert)
